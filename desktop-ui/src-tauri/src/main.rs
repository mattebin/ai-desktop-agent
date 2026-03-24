#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use std::ffi::OsString;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::{Manager, RunEvent};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

const DEFAULT_LOCAL_API_HOST: &str = "127.0.0.1";
const DEFAULT_LOCAL_API_PORT: u16 = 8765;
const DEFAULT_OPERATOR_MODEL: &str = "gpt-5.4";
const DEFAULT_REASONING_EFFORT: &str = "medium";

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

struct ManagedApiProcess {
    child: Child,
    base_url: String,
    owner_token: String,
    owner_pid: u32,
    child_pid: u32,
    backend_log_path: PathBuf,
}

struct ApiProcessState {
    process: Mutex<Option<ManagedApiProcess>>,
    owner_token: String,
    owner_pid: u32,
    shutdown_started: AtomicBool,
    runtime_status: Mutex<DesktopRuntimeStatus>,
    log_path: PathBuf,
    backend_log_path: Mutex<Option<PathBuf>>,
}

impl Default for ApiProcessState {
    fn default() -> Self {
        Self {
            process: Mutex::new(None),
            owner_token: generate_owner_token(),
            owner_pid: process::id(),
            shutdown_started: AtomicBool::new(false),
            runtime_status: Mutex::new(DesktopRuntimeStatus::default()),
            log_path: default_runtime_log_path(),
            backend_log_path: Mutex::new(None),
        }
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct EnsureLocalApiResponse {
    base_url: String,
    started: bool,
    managed_by_desktop: bool,
    runtime_status: DesktopRuntimeStatus,
    log_path: String,
    backend_log_path: Option<String>,
}

#[derive(Serialize, Clone, Default)]
#[serde(rename_all = "camelCase")]
struct DesktopRuntimeStatus {
    backend_state: String,
    decision: String,
    detail: String,
    base_url: String,
    attached: bool,
    managed_by_desktop: bool,
    ownership_confirmed: bool,
    api_pid: Option<u32>,
    child_pid: Option<u32>,
}

#[derive(Deserialize, Default)]
struct HealthEnvelope {
    ok: bool,
    data: HealthPayload,
}

#[derive(Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct HealthPayload {
    runtime: Option<RuntimePayload>,
    management: Option<ManagementPayload>,
}

#[derive(Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct RuntimePayload {
    active_model: Option<String>,
    reasoning_effort: Option<String>,
    settings_path: Option<String>,
}

#[derive(Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct ManagementPayload {
    managed_by_desktop: Option<bool>,
    owner_token: Option<String>,
    owner_pid: Option<u32>,
    api_pid: Option<u32>,
}

#[derive(Deserialize, Default)]
struct SettingsFile {
    model: Option<String>,
    reasoning: Option<ReasoningSettings>,
    reasoning_effort: Option<String>,
}

#[derive(Deserialize, Default)]
struct ReasoningSettings {
    effort: Option<String>,
}

struct DesiredRuntime {
    active_model: String,
    reasoning_effort: String,
    settings_path: String,
}

struct SpawnedLocalApi {
    child: Child,
    backend_log_path: PathBuf,
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(ApiProcessState::default())
        .invoke_handler(tauri::generate_handler![ensure_local_api, shutdown_local_api])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            let state = app_handle.state::<ApiProcessState>();
            if state
                .shutdown_started
                .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
                .is_ok()
            {
                let _ = shutdown_owned_api_process(state.inner());
            }
        }
    });
}

#[tauri::command]
fn ensure_local_api(state: tauri::State<'_, ApiProcessState>) -> Result<EnsureLocalApiResponse, String> {
    let host = DEFAULT_LOCAL_API_HOST.to_string();
    let default_port = DEFAULT_LOCAL_API_PORT;
    let default_base_url = local_api_base_url(&host, default_port);
    let repo_root = find_repo_root()
        .ok_or_else(|| "Unable to locate the operator project root for local API startup.".to_string())?;
    let desired_runtime = load_desired_runtime(&repo_root);

    {
        let mut guard = state.process.lock().map_err(|_| "Unable to lock local API process state.")?;
        if let Some(process) = guard.as_mut() {
            let still_running = process
                .child
                .try_wait()
                .map_err(|error| format!("Unable to inspect local API process: {error}"))?
                .is_none();

            if still_running
                && process.is_owned_by(&state.owner_token, state.owner_pid)
                && api_matches_desired_runtime(&process.base_url, &desired_runtime)
                && api_matches_managed_process(&process.base_url, process)
            {
                let runtime_status = commit_runtime_status(
                    state.inner(),
                    DesktopRuntimeStatus {
                        backend_state: "app_managed".to_string(),
                        decision: "reused_owned_child".to_string(),
                        detail: "Reused the desktop-owned local API child from this app instance.".to_string(),
                        base_url: process.base_url.clone(),
                        attached: true,
                        managed_by_desktop: true,
                        ownership_confirmed: true,
                        api_pid: Some(process.child_pid),
                        child_pid: Some(process.child_pid),
                    },
                );
                return Ok(EnsureLocalApiResponse {
                    base_url: process.base_url.clone(),
                    started: false,
                    managed_by_desktop: true,
                    log_path: normalize_path_string(&state.log_path),
                    backend_log_path: Some(normalize_path_string(&process.backend_log_path)),
                    runtime_status,
                });
            }
        }
        let release = release_managed_process(&mut guard, &state.owner_token, state.owner_pid)?;
        if !matches!(release, ManagedProcessRelease::ReleasedUnowned { .. }) {
            set_backend_log_path(state.inner(), None);
        }
        log_release_decision(state.inner(), &release, "bootstrap_refresh");
    }

    let default_health = api_health(&default_base_url);
    if default_health
        .as_ref()
        .map(|health| health_matches_desired_runtime(health, &desired_runtime))
        .unwrap_or(false)
    {
        let health = default_health.as_ref().expect("default health should exist when runtime matches");
        let runtime_status = commit_runtime_status(
            state.inner(),
            describe_existing_backend(
                health,
                &default_base_url,
                "attached_existing_backend",
                "Attached to an already-running compatible backend without taking ownership.",
            ),
        );
        return Ok(EnsureLocalApiResponse {
            base_url: default_base_url,
            started: false,
            managed_by_desktop: runtime_status.managed_by_desktop,
            log_path: normalize_path_string(&state.log_path),
            backend_log_path: current_backend_log_path(state.inner()).map(|path| normalize_path_string(&path)),
            runtime_status,
        });
    }

    if let Some(health) = default_health.as_ref() {
        commit_runtime_status(
            state.inner(),
            describe_existing_backend(
                health,
                &default_base_url,
                "ignored_existing_backend",
                "Detected an existing backend on the default port, but it was incompatible or detached, so the desktop host started a separate owned backend instead.",
            ),
        );
    } else {
        commit_runtime_status(
            state.inner(),
            DesktopRuntimeStatus {
                backend_state: "missing".to_string(),
                decision: "no_compatible_backend_found".to_string(),
                detail: "No compatible backend was available, so the desktop host is starting one.".to_string(),
                base_url: default_base_url.clone(),
                attached: false,
                managed_by_desktop: false,
                ownership_confirmed: false,
                api_pid: None,
                child_pid: None,
            },
        );
    }

    let spawn_port = if port_available(&host, default_port) {
        default_port
    } else {
        pick_free_port(&host)?
    };
    let base_url = local_api_base_url(&host, spawn_port);

    {
        let mut guard = state.process.lock().map_err(|_| "Unable to lock local API process state.")?;
        let spawned = match spawn_local_api(&repo_root, &host, spawn_port, &state.owner_token, state.owner_pid) {
            Ok(child) => child,
            Err(error) => {
                commit_runtime_status(
                    state.inner(),
                    DesktopRuntimeStatus {
                        backend_state: "missing".to_string(),
                        decision: "failed_to_launch_owned_child".to_string(),
                        detail: format!("The desktop host could not launch its owned backend child: {error}"),
                        base_url: base_url.clone(),
                        attached: false,
                        managed_by_desktop: false,
                        ownership_confirmed: false,
                        api_pid: None,
                        child_pid: None,
                    },
                );
                return Err(error);
            }
        };
        let child_pid = spawned.child.id();
        set_backend_log_path(state.inner(), Some(spawned.backend_log_path.clone()));
        *guard = Some(ManagedApiProcess {
            child_pid,
            child: spawned.child,
            base_url: base_url.clone(),
            owner_token: state.owner_token.clone(),
            owner_pid: state.owner_pid,
            backend_log_path: spawned.backend_log_path,
        });
    }

    thread::sleep(Duration::from_millis(450));
    let exited_early = state
        .process
        .lock()
        .ok()
        .and_then(|mut guard| {
            guard
                .as_mut()
                .and_then(|process| process.child.try_wait().ok().flatten().map(|status| format!("{status}")))
        });
    if let Some(status) = exited_early {
        let diagnostics = startup_failure_diagnostics(state.inner(), &base_url, &desired_runtime);
        let _ = shutdown_owned_api_process(state.inner());
        let error = format!("The desktop-owned backend child exited before the local API became reachable. Exit status: {status}.{diagnostics}");
        commit_runtime_status(
            state.inner(),
            DesktopRuntimeStatus {
                backend_state: "unhealthy".to_string(),
                decision: "failed_to_start_owned_child".to_string(),
                detail: error.clone(),
                base_url: base_url.clone(),
                attached: false,
                managed_by_desktop: false,
                ownership_confirmed: false,
                api_pid: None,
                child_pid: None,
            },
        );
        return Err(error);
    }

    let runtime_status = commit_runtime_status(
        state.inner(),
        DesktopRuntimeStatus {
            backend_state: "app_managed".to_string(),
            decision: if spawn_port == default_port {
                "started_owned_child".to_string()
            } else {
                "started_owned_child_on_safe_port".to_string()
            },
            detail: if spawn_port == default_port {
                "Started a desktop-owned local API on the default port. The desktop UI will wait for the API to become reachable before loading conversations.".to_string()
            } else {
                "Started a desktop-owned local API on a free local port because the default port was unavailable. The desktop UI will wait for the API to become reachable before loading conversations.".to_string()
            },
            base_url: base_url.clone(),
            attached: true,
            managed_by_desktop: true,
            ownership_confirmed: true,
            api_pid: None,
            child_pid: current_managed_child_pid(state.inner()),
        },
    );

    Ok(EnsureLocalApiResponse {
        base_url,
        started: true,
        managed_by_desktop: true,
        log_path: normalize_path_string(&state.log_path),
        backend_log_path: current_backend_log_path(state.inner()).map(|path| normalize_path_string(&path)),
        runtime_status,
    })
}

#[tauri::command]
fn shutdown_local_api(state: tauri::State<'_, ApiProcessState>) -> Result<bool, String> {
    shutdown_owned_api_process(state.inner())
}

fn local_api_base_url(host: &str, port: u16) -> String {
    format!("http://{host}:{port}")
}

fn api_health(base_url: &str) -> Option<HealthPayload> {
    let (host, port) = parse_local_api_endpoint(base_url)?;
    let address = format!("{host}:{port}");
    let mut stream = TcpStream::connect_timeout(&address.parse().ok()?, Duration::from_millis(900)).ok()?;
    let _ = stream.set_read_timeout(Some(Duration::from_millis(900)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(900)));
    stream
        .write_all(
            format!(
                "GET /health HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\nAccept: application/json\r\n\r\n"
            )
            .as_bytes(),
        )
        .ok()?;

    let body = read_http_json_body(&mut stream)?;
    let parsed = serde_json::from_str::<HealthEnvelope>(&body).ok()?;
    if !parsed.ok {
        return None;
    }
    Some(parsed.data)
}

fn parse_local_api_endpoint(base_url: &str) -> Option<(String, u16)> {
    let trimmed = base_url.trim().trim_end_matches('/');
    let target = trimmed.strip_prefix("http://")?;
    let (host, port) = target.rsplit_once(':')?;
    let port = port.parse::<u16>().ok()?;
    Some((host.to_string(), port))
}

fn read_http_json_body(stream: &mut TcpStream) -> Option<String> {
    let mut response = Vec::new();
    let mut content_length: Option<usize> = None;
    let separator = b"\r\n\r\n";
    let mut buffer = [0_u8; 4096];

    loop {
        let bytes_read = stream.read(&mut buffer).ok()?;
        if bytes_read == 0 {
            break;
        }
        response.extend_from_slice(&buffer[..bytes_read]);
        if let Some(index) = response.windows(separator.len()).position(|window| window == separator) {
            if content_length.is_none() {
                let headers = String::from_utf8(response[..index].to_vec()).ok()?;
                content_length = headers
                    .lines()
                    .find_map(|line| {
                        let (name, value) = line.split_once(':')?;
                        if name.trim().eq_ignore_ascii_case("content-length") {
                            return value.trim().parse::<usize>().ok();
                        }
                        None
                    });
            }
            let body = &response[index + separator.len()..];
            if let Some(expected_length) = content_length {
                if body.len() >= expected_length {
                    return String::from_utf8(body[..expected_length].to_vec()).ok();
                }
            }
        }
    }

    extract_http_json_body(&response)
}

fn extract_http_json_body(response: &[u8]) -> Option<String> {
    let separator = b"\r\n\r\n";
    let index = response.windows(separator.len()).position(|window| window == separator)?;
    let body = &response[index + separator.len()..];
    String::from_utf8(body.to_vec()).ok()
}

fn port_available(host: &str, port: u16) -> bool {
    TcpListener::bind((host, port)).map(|listener| {
        drop(listener);
        true
    }).unwrap_or(false)
}

fn spawn_local_api(repo_root: &Path, host: &str, port: u16, owner_token: &str, owner_pid: u32) -> Result<SpawnedLocalApi, String> {
    let python = locate_python(repo_root).unwrap_or_else(|| OsString::from("python"));
    let main_py = repo_root.join("main.py");
    if !main_py.exists() {
        return Err("The local operator main.py entrypoint was not found.".to_string());
    }

    let backend_log_path = next_backend_log_path(repo_root);
    if let Some(parent) = backend_log_path.parent() {
        fs::create_dir_all(parent).map_err(|error| format!("Unable to prepare local API log directory: {error}"))?;
    }
    let mut log_file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&backend_log_path)
        .map_err(|error| format!("Unable to open the local API backend log: {error}"))?;
    let stdout_log = log_file
        .try_clone()
        .map_err(|error| format!("Unable to prepare the local API stdout log: {error}"))?;
    let stderr_log = log_file
        .try_clone()
        .map_err(|error| format!("Unable to prepare the local API stderr log: {error}"))?;
    let _ = writeln!(
        log_file,
        "[bootstrap] repo_root={} python={} main={} host={} port={} owner_token={} owner_pid={}",
        repo_root.display(),
        PathBuf::from(&python).display(),
        main_py.display(),
        host,
        port,
        owner_token,
        owner_pid,
    );

    let mut command = Command::new(python);
    command
        .current_dir(repo_root)
        .arg(main_py)
        .arg("--api")
        .arg("--api-host")
        .arg(host)
        .arg("--api-port")
        .arg(port.to_string())
        .env("AI_OPERATOR_DESKTOP_MANAGED", "1")
        .env("AI_OPERATOR_DESKTOP_OWNER_TOKEN", owner_token)
        .env("AI_OPERATOR_DESKTOP_OWNER_PID", owner_pid.to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout_log))
        .stderr(Stdio::from(stderr_log));

    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);

    let child = command
        .spawn()
        .map_err(|error| format!("Unable to launch the local API process: {error}"))?;

    Ok(SpawnedLocalApi {
        child,
        backend_log_path,
    })
}

fn pick_free_port(host: &str) -> Result<u16, String> {
    let listener = TcpListener::bind((host, 0)).map_err(|error| format!("Unable to reserve a free local API port: {error}"))?;
    let port = listener
        .local_addr()
        .map_err(|error| format!("Unable to inspect reserved local API port: {error}"))?
        .port();
    drop(listener);
    Ok(port)
}

fn locate_python(repo_root: &Path) -> Option<OsString> {
    let candidates = [
        repo_root.join(".venv").join("Scripts").join("python.exe"),
        repo_root.join(".venv").join("bin").join("python"),
    ];

    candidates
        .into_iter()
        .find(|path| path.exists())
        .map(|path| path.into_os_string())
}

fn looks_like_repo_root(path: &Path) -> bool {
    path.join("main.py").exists() && path.join("core").join("local_api.py").exists()
}

fn candidate_roots() -> Vec<PathBuf> {
    let mut candidates = Vec::new();

    if let Ok(current_dir) = std::env::current_dir() {
        candidates.push(current_dir);
    }

    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    candidates.push(manifest_dir.clone());
    if let Some(parent) = manifest_dir.parent() {
        candidates.push(parent.to_path_buf());
        if let Some(grandparent) = parent.parent() {
            candidates.push(grandparent.to_path_buf());
        }
    }

    if let Ok(executable) = std::env::current_exe() {
        if let Some(parent) = executable.parent() {
            candidates.push(parent.to_path_buf());
        }
    }

    candidates
}

fn find_repo_root() -> Option<PathBuf> {
    for candidate in candidate_roots() {
        for ancestor in candidate.ancestors() {
            if looks_like_repo_root(ancestor) {
                return Some(ancestor.to_path_buf());
            }
        }
    }
    None
}

fn load_desired_runtime(repo_root: &Path) -> DesiredRuntime {
    let settings_path = repo_root.join("config").join("settings.yaml");
    let settings = std::fs::read_to_string(&settings_path)
        .ok()
        .and_then(|body| serde_yaml::from_str::<SettingsFile>(&body).ok())
        .unwrap_or_default();

    let active_model = settings
        .model
        .unwrap_or_else(|| DEFAULT_OPERATOR_MODEL.to_string())
        .trim()
        .to_string();
    let reasoning_effort = settings
        .reasoning
        .as_ref()
        .and_then(|reasoning| reasoning.effort.clone())
        .or(settings.reasoning_effort)
        .unwrap_or_else(|| DEFAULT_REASONING_EFFORT.to_string())
        .trim()
        .to_string();

    DesiredRuntime {
        active_model: normalize_text(&active_model),
        reasoning_effort: normalize_text(&reasoning_effort),
        settings_path: normalize_path_string(&settings_path),
    }
}

fn api_matches_desired_runtime(base_url: &str, desired_runtime: &DesiredRuntime) -> bool {
    api_health(base_url)
        .as_ref()
        .map(|health| health_matches_desired_runtime(health, desired_runtime))
        .unwrap_or(false)
}

fn health_matches_desired_runtime(health: &HealthPayload, desired_runtime: &DesiredRuntime) -> bool {
    let Some(runtime) = health.runtime.as_ref() else {
        return false;
    };
    let active_model = normalize_text(runtime.active_model.as_deref().unwrap_or(""));
    let reasoning_effort = normalize_text(runtime.reasoning_effort.as_deref().unwrap_or(""));
    let settings_path = normalize_path_string(Path::new(runtime.settings_path.as_deref().unwrap_or("")));

    active_model == desired_runtime.active_model
        && reasoning_effort == desired_runtime.reasoning_effort
        && !settings_path.is_empty()
        && settings_path == desired_runtime.settings_path
}

fn describe_existing_backend(
    health: &HealthPayload,
    base_url: &str,
    decision: &str,
    detail: &str,
) -> DesktopRuntimeStatus {
    let management = health.management.as_ref();
    let claims_desktop = management
        .and_then(|payload| payload.managed_by_desktop)
        .unwrap_or(false);

    DesktopRuntimeStatus {
        backend_state: if claims_desktop {
            "detached".to_string()
        } else {
            "externally_managed".to_string()
        },
        decision: decision.to_string(),
        detail: detail.to_string(),
        base_url: base_url.to_string(),
        attached: true,
        managed_by_desktop: false,
        ownership_confirmed: false,
        api_pid: infer_api_pid_from_health(health),
        child_pid: None,
    }
}

fn api_matches_managed_process(base_url: &str, process: &ManagedApiProcess) -> bool {
    let Some(health) = api_health(base_url) else {
        return false;
    };
    let Some(management) = health.management else {
        return false;
    };
    if !management.managed_by_desktop.unwrap_or(false) {
        return false;
    }

    let owner_token = normalize_text(management.owner_token.as_deref().unwrap_or(""));
    let owner_pid = management.owner_pid.unwrap_or_default();
    let api_pid = management.api_pid.unwrap_or_default();

    owner_token == normalize_text(&process.owner_token)
        && owner_pid == process.owner_pid
        && (api_pid == 0 || api_pid == process.child_pid)
}

fn shutdown_owned_api_process(state: &ApiProcessState) -> Result<bool, String> {
    let mut guard = state
        .process
        .lock()
        .map_err(|_| "Unable to lock local API process state.")?;
    let release = release_managed_process(&mut guard, &state.owner_token, state.owner_pid)?;
    if !matches!(release, ManagedProcessRelease::ReleasedUnowned { .. }) {
        set_backend_log_path(state, None);
    }
    let stopped = matches!(release, ManagedProcessRelease::Stopped { .. });
    log_release_decision(state, &release, "shutdown");
    Ok(stopped)
}

enum ManagedProcessRelease {
    NotPresent,
    AlreadyExited { base_url: String, child_pid: Option<u32> },
    Stopped { base_url: String, child_pid: Option<u32> },
    ReleasedUnowned { base_url: String, child_pid: Option<u32> },
}

fn release_managed_process(
    slot: &mut Option<ManagedApiProcess>,
    owner_token: &str,
    owner_pid: u32,
) -> Result<ManagedProcessRelease, String> {
    let Some(mut process) = slot.take() else {
        return Ok(ManagedProcessRelease::NotPresent);
    };

    let base_url = process.base_url.clone();
    let child_pid = Some(process.child_pid);

    if !process.is_owned_by(owner_token, owner_pid) {
        return Ok(ManagedProcessRelease::ReleasedUnowned { base_url, child_pid });
    }

    if process
        .child
        .try_wait()
        .map_err(|error| format!("Unable to inspect local API process: {error}"))?
        .is_some()
    {
        return Ok(ManagedProcessRelease::AlreadyExited { base_url, child_pid });
    }

    process
        .child
        .kill()
        .map_err(|error| format!("Unable to stop local API process: {error}"))?;
    let _ = process.child.wait();
    Ok(ManagedProcessRelease::Stopped { base_url, child_pid })
}

impl ManagedApiProcess {
    fn is_owned_by(&self, owner_token: &str, owner_pid: u32) -> bool {
        self.owner_pid == owner_pid
            && self.child_pid == self.child.id()
            && normalize_text(&self.owner_token) == normalize_text(owner_token)
    }
}

fn generate_owner_token() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    format!("desktop-ui-{}-{nanos}", process::id())
}

fn current_managed_child_pid(state: &ApiProcessState) -> Option<u32> {
    state
        .process
        .lock()
        .ok()
        .and_then(|guard| guard.as_ref().map(|process| process.child_pid))
}

fn current_backend_log_path(state: &ApiProcessState) -> Option<PathBuf> {
    state
        .backend_log_path
        .lock()
        .ok()
        .and_then(|guard| guard.as_ref().cloned())
}

fn set_backend_log_path(state: &ApiProcessState, value: Option<PathBuf>) {
    if let Ok(mut guard) = state.backend_log_path.lock() {
        *guard = value;
    }
}

fn next_backend_log_path(repo_root: &Path) -> PathBuf {
    let timestamp_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    repo_root
        .join("data")
        .join("local_api_runtime")
        .join(format!("local-api-{timestamp_ms}.log"))
}

fn infer_api_pid_from_health(health: &HealthPayload) -> Option<u32> {
    health.management.as_ref().and_then(|payload| payload.api_pid)
}

fn default_runtime_log_path() -> PathBuf {
    if let Some(repo_root) = find_repo_root() {
        return repo_root.join("data").join("desktop_runtime_events.jsonl");
    }
    std::env::temp_dir().join("ai-operator-desktop-runtime-events.jsonl")
}

fn commit_runtime_status(state: &ApiProcessState, status: DesktopRuntimeStatus) -> DesktopRuntimeStatus {
    if let Ok(mut guard) = state.runtime_status.lock() {
        *guard = status.clone();
    }
    append_runtime_log(state, &status);
    status
}

fn append_runtime_log(state: &ApiProcessState, status: &DesktopRuntimeStatus) {
    #[derive(Serialize)]
    struct RuntimeAuditEntry<'a> {
        timestamp_ms: u128,
        owner_pid: u32,
        owner_token: &'a str,
        backend_state: &'a str,
        decision: &'a str,
        detail: &'a str,
        base_url: &'a str,
        attached: bool,
        managed_by_desktop: bool,
        ownership_confirmed: bool,
        api_pid: Option<u32>,
        child_pid: Option<u32>,
    }

    let entry = RuntimeAuditEntry {
        timestamp_ms: SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis(),
        owner_pid: state.owner_pid,
        owner_token: &state.owner_token,
        backend_state: &status.backend_state,
        decision: &status.decision,
        detail: &status.detail,
        base_url: &status.base_url,
        attached: status.attached,
        managed_by_desktop: status.managed_by_desktop,
        ownership_confirmed: status.ownership_confirmed,
        api_pid: status.api_pid,
        child_pid: status.child_pid,
    };

    if let Some(parent) = state.log_path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if let Ok(line) = serde_json::to_string(&entry) {
        if let Ok(mut file) = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&state.log_path)
        {
            let _ = writeln!(file, "{line}");
        }
        eprintln!("[desktop-runtime] {line}");
    }
}

fn describe_runtime_observation(health: &HealthPayload, desired_runtime: &DesiredRuntime) -> String {
    let runtime = health.runtime.as_ref();
    let active_model = normalize_text(runtime.and_then(|value| value.active_model.as_deref()).unwrap_or(""));
    let reasoning_effort = normalize_text(runtime.and_then(|value| value.reasoning_effort.as_deref()).unwrap_or(""));
    let settings_path = normalize_path_string(Path::new(runtime.and_then(|value| value.settings_path.as_deref()).unwrap_or("")));

    format!(
        "Observed runtime model='{active_model}', reasoning='{reasoning_effort}', settings='{settings_path}'. Expected model='{}', reasoning='{}', settings='{}'.",
        desired_runtime.active_model,
        desired_runtime.reasoning_effort,
        desired_runtime.settings_path,
    )
}

fn read_log_tail(path: &Path, limit: usize) -> String {
    fs::read_to_string(path)
        .ok()
        .map(|body| {
            let lines = body.lines().collect::<Vec<_>>();
            let start = lines.len().saturating_sub(limit);
            lines[start..].join(" | ")
        })
        .unwrap_or_default()
}

fn startup_failure_diagnostics(state: &ApiProcessState, base_url: &str, desired_runtime: &DesiredRuntime) -> String {
    let process_state = state
        .process
        .lock()
        .ok()
        .and_then(|mut guard| {
            guard.as_mut().and_then(|process| {
                process
                    .child
                    .try_wait()
                    .ok()
                    .flatten()
                    .map(|status| format!(" Child exited early with status: {status}."))
            })
        })
        .unwrap_or_default();

    let observed = api_health(base_url)
        .as_ref()
        .map(|health| format!(" {}", describe_runtime_observation(health, desired_runtime)))
        .unwrap_or_else(|| " No matching /health response was available at failure time.".to_string());

    let backend_log = current_backend_log_path(state)
        .map(|path| {
            let tail = read_log_tail(&path, 16);
            if tail.is_empty() {
                format!(" Backend log: {}.", path.display())
            } else {
                format!(" Backend log: {}. Tail: {}.", path.display(), tail)
            }
        })
        .unwrap_or_default();

    format!("{process_state}{observed}{backend_log}")
}

fn log_release_decision(state: &ApiProcessState, release: &ManagedProcessRelease, phase: &str) {
    let status = match release {
        ManagedProcessRelease::NotPresent => DesktopRuntimeStatus {
            backend_state: "missing".to_string(),
            decision: format!("{phase}_no_managed_child"),
            detail: "There was no desktop-managed backend child to stop or release.".to_string(),
            base_url: String::new(),
            attached: false,
            managed_by_desktop: false,
            ownership_confirmed: false,
            api_pid: None,
            child_pid: None,
        },
        ManagedProcessRelease::AlreadyExited { base_url, child_pid } => DesktopRuntimeStatus {
            backend_state: "missing".to_string(),
            decision: format!("{phase}_cleared_exited_owned_child"),
            detail: "The desktop-managed backend child had already exited, so the stale record was cleared.".to_string(),
            base_url: base_url.clone(),
            attached: false,
            managed_by_desktop: false,
            ownership_confirmed: true,
            api_pid: None,
            child_pid: *child_pid,
        },
        ManagedProcessRelease::Stopped { base_url, child_pid } => DesktopRuntimeStatus {
            backend_state: "missing".to_string(),
            decision: format!("{phase}_stopped_owned_child"),
            detail: "Stopped the desktop-managed backend child owned by this app instance.".to_string(),
            base_url: base_url.clone(),
            attached: false,
            managed_by_desktop: false,
            ownership_confirmed: true,
            api_pid: None,
            child_pid: *child_pid,
        },
        ManagedProcessRelease::ReleasedUnowned { base_url, child_pid } => DesktopRuntimeStatus {
            backend_state: "detached".to_string(),
            decision: format!("{phase}_released_unowned_record"),
            detail: "Released a backend record without stopping it because ownership could not be confirmed.".to_string(),
            base_url: base_url.clone(),
            attached: false,
            managed_by_desktop: false,
            ownership_confirmed: false,
            api_pid: None,
            child_pid: *child_pid,
        },
    };
    let _ = commit_runtime_status(state, status);
}

fn normalize_text(value: &str) -> String {
    value.trim().to_ascii_lowercase()
}

fn normalize_path_string(path: &Path) -> String {
    let resolved = std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
    let normalized = resolved.to_string_lossy().replace('/', "\\");
    normalized
        .trim_start_matches(r"\\?\")
        .to_ascii_lowercase()
}
