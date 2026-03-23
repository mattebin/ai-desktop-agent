# Agents

## What a sub-agent is in this repo

A sub-agent is a bounded role or responsibility definition used to structure specialized reasoning or evaluation inside the operator system.

Examples of possible sub-agent roles:

- planner
- evaluator
- browser researcher
- desktop inspector
- final-answer synthesizer
- recommendation critic

## What sub-agents are for

Sub-agents should help with:

- clearer responsibility boundaries
- better reasoning specialization
- easier testing and evaluation
- less prompt sprawl
- safer capability expansion

## Design principles

Sub-agents in this project should:

- stay bounded
- serve the main operator architecture
- not become an excuse for a broad rewrite
- not introduce dangerous autonomy casually
- remain understandable and controllable

## Suggested sub-agent file format

A sub-agent definition should usually include:

- role
- goal
- allowed scope
- forbidden scope
- expected inputs
- expected outputs
- decision rules
- safety/approval constraints

## Naming suggestions

Use descriptive names such as:

- `planner.md`
- `desktop_inspector.md`
- `browser_researcher.md`
- `final_answer_reviewer.md`
- `recommendation_evaluator.md`

Current repo-local desktop roles now also include:

- `desktop_inspector.md`
- `desktop_recovery_planner.md`
