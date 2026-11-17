"""The console's resolved team configuration (geeera/team-console ADR 0009) as the plugin reads it inside a job.

Inside a console job `backlog next`, `pr gate` and `slot-context` read `GET …/team-config/resolved` once per run
and act on it: caps and freeze days, disabled roles, tier folding and the gate policy. Every read fails closed: an
unreadable or invalid view is a refusal with the reason, never a fallback to `project.yml`, whose `review`, `caps`,
`sprint` and `owner.language` are import-only copies on a console-tracker project (ADR 0009 d.11, threat model
T9/T10). `pr gate` outside a job refuses to judge for the same reason (team-console#683).

The plugin cannot import the console's TypeScript guard (`isResolvedTeamConfig` in `@shared/contracts`), so
`check` mirrors it: an exact-key walk where every member is required, every value is an allow-list or a bounded
integer, and an unknown key is refused with its path. The specs replay the vendored fixtures through it.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Tuple

from . import console, review

SCHEMA = "team-config/v1"
ROUTE = ("team-config", "resolved")

# team-console libs/shared/contracts/src/lib/team-config.ts, verbatim; the fixture spec keeps them equal.
TEAM_ROLES = ("pm", "architect", "orchestrator", "dev", "dev-light", "dev-heavy", "qa", "reviewer", "security",
              "devops", "analyst", "scribe", "ux-designer", "ui-designer")
MODEL_ALLOW_LIST = ("claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5", "claude-haiku-4-5-20251001")
EFFORTS = ("low", "medium", "high")
ACCESS_SOURCES = ("subscription", "api_key")
GATE_POLICIES = ("always", "code")
LANGUAGES = ("ru", "en")
REVIEWER_CONTEXTS = ("diff", "repo")
CAP_MODES = ("normal", "burn", "freeze")
# The roles the gate rests on, plus `security`, which is never switched off either (ADR 0009 d.2).
NEVER_OFF_ROLES = frozenset({"orchestrator", "pm", "dev", "qa", "reviewer", "security"})
BOUNDS = {
    "max_parallel": (1, 5), "max_minutes": (1, 720), "max_turns": (1, 1000), "max_rework_rounds": (0, 3),
    "dev_tasks": (0, 10), "parallel_devs": (1, 5), "length_days": (7, 28), "freeze_days": (0, 7),
    "code_paths": (1, 20), "code_path_length": (1, 200), "revision": (0, 2 ** 53 - 1),
}
# Which console role an `agent:<name>` label or a development tier runs as.
AGENT_ROLES = {"fullstack-dev": "dev", "fullstack-dev-light": "dev-light", "fullstack-dev-heavy": "dev-heavy",
               "qa-runner": "qa"}
JOB_ENV = ("TC_API_URL", "TC_JOB_TOKEN", "TC_PROJECT")
_PRINTABLE_KEY = re.compile(r"[^\x21-\x7e]")


class TeamConfigInvalid(ValueError):
    """A resolved view the plugin refuses to act on; `field` is the failing path (`roles.dev.temperature`)."""

    def __init__(self, field: str, detail: str):
        self.field, self.detail = field, detail
        super().__init__(f"{field}: {detail}")


Check = Callable[[Any, str], None]


def _path(parent: str, key: str) -> str:
    # A key is the console's data: printable ASCII only and clipped, so a reason line cannot be forged with it.
    shown = _PRINTABLE_KEY.sub("?", key)[:60]
    return f"{parent}.{shown}" if parent else shown


def _section(fields: Mapping[str, Check]) -> Check:
    def check(value: Any, path: str) -> None:
        if not isinstance(value, dict):
            raise TeamConfigInvalid(path or "view", "must be an object")
        for key in value:
            if key not in fields:
                raise TeamConfigInvalid(_path(path, str(key)), "is not a member of team-config/v1")
        for key, inner in fields.items():
            if key not in value:
                raise TeamConfigInvalid(_path(path, key), "is required")
            inner(value[key], _path(path, key))
    return check


def _integer(bound: str) -> Check:
    low, high = BOUNDS[bound]

    def check(value: Any, path: str) -> None:
        # bool is an int in Python and 2.0 a float: neither is a whole number on the wire.
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise TeamConfigInvalid(path, f"must be a whole number from {low} to {high}")
    return check


def _one_of(values: Tuple[str, ...]) -> Check:
    def check(value: Any, path: str) -> None:
        if not isinstance(value, str) or value not in values:
            raise TeamConfigInvalid(path, f"must be one of {', '.join(values)}")
    return check


def _boolean(value: Any, path: str) -> None:
    if not isinstance(value, bool):
        raise TeamConfigInvalid(path, "must be true or false")


def _schema(value: Any, path: str) -> None:
    if value != SCHEMA:
        raise TeamConfigInvalid(path, f"must be {SCHEMA}")


def _code_paths(value: Any, path: str) -> None:
    low, high = BOUNDS["code_paths"]
    _, longest = BOUNDS["code_path_length"]
    if not isinstance(value, list) or not low <= len(value) <= high:
        raise TeamConfigInvalid(path, f"must be a list of {low} to {high} globs")
    for index, glob in enumerate(value):
        at = f"{path}[{index}]"
        if not isinstance(glob, str) or not 1 <= len(glob) <= longest or ".." in glob or glob.startswith("/") \
                or "\\" in glob or any(ord(c) < 0x20 or ord(c) == 0x7f for c in glob):
            raise TeamConfigInvalid(at, f"must be a relative glob of at most {longest} characters without .., a "
                                        "leading / or \\")


def _allowed_models(role: str) -> Tuple[str, ...]:
    """`security` never on Fable (SPEC decision 13); only `scribe` on Haiku."""
    return tuple(m for m in MODEL_ALLOW_LIST
                 if (m != "claude-haiku-4-5-20251001" or role == "scribe")
                 and (m != "claude-fable-5-1" or role != "security"))


def _role(role: str) -> Check:
    def enabled(value: Any, path: str) -> None:
        _boolean(value, path)
        if value is False and role in NEVER_OFF_ROLES:
            raise TeamConfigInvalid(path, f"must be true: the {role} role is never switched off")

    return _section({
        "enabled": enabled, "model": _one_of(_allowed_models(role)), "effort": _one_of(EFFORTS),
        "max_parallel": _integer("max_parallel"), "max_minutes": _integer("max_minutes"),
        "max_turns": _integer("max_turns"),
    })


_CAP = _section({"dev_tasks": _integer("dev_tasks"), "parallel_devs": _integer("parallel_devs")})
_VIEW = _section({
    "schema": _schema,
    "revision": _integer("revision"),
    "access": _section({"source": _one_of(ACCESS_SOURCES)}),
    "roles": _section({role: _role(role) for role in TEAM_ROLES}),
    "gates": _section({"qa": _one_of(GATE_POLICIES), "reviewer": _one_of(GATE_POLICIES), "code_paths": _code_paths,
                       "max_rework_rounds": _integer("max_rework_rounds")}),
    "caps": _section({mode: _CAP for mode in CAP_MODES}),
    "sprint": _section({"length_days": _integer("length_days"), "freeze_days": _integer("freeze_days")}),
    "language": _one_of(LANGUAGES),
    "cost": _section({"prompt_caching": _boolean, "batch_api": _boolean,
                      "reviewer_context": _one_of(REVIEWER_CONTEXTS)}),
})


def check(value: Any) -> Dict[str, Any]:
    """The view as a fresh copy when it is exactly `ResolvedTeamConfigDto`; TeamConfigInvalid otherwise. The schema
    and the revision are checked first, so a view of another version is refused as that, not as its members."""
    if not isinstance(value, dict):
        raise TeamConfigInvalid("view", "must be an object")
    for key, inner in (("schema", _schema), ("revision", _integer("revision"))):
        if key not in value:
            raise TeamConfigInvalid(key, "is required")
        inner(value[key], key)
    _VIEW(value, "")
    if value["sprint"]["freeze_days"] >= value["sprint"]["length_days"]:
        raise TeamConfigInvalid("sprint.freeze_days", "must be shorter than sprint.length_days")
    return json.loads(json.dumps(value))


def in_job(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Any of the job variables set: the run means to be a console job, so a missing one is a refusal, not a
    reason to read `project.yml`."""
    env = os.environ if environ is None else environ
    return any(env.get(name, "").strip() for name in JOB_ENV)


def load() -> Dict[str, Any]:
    """`GET …/team-config/resolved`, checked. ConsoleError or TeamConfigInvalid; callers fail closed on either."""
    dto, _ = console.request("GET", console.project_path(*ROUTE))
    return check(dto)


OUTSIDE_JOB = ("team config: not inside a console job (TC_API_URL, TC_JOB_TOKEN and TC_PROJECT are unset) on a "
               "console-tracker project (`tracker: console` in project.yml on the base branch): the gate policy is "
               "the owner's team config in the console (ADR 0009 d.11), which this run has no owner-scoped way to "
               "read, so {what} refuses to judge rather than use project.yml's review block. Run it inside a "
               "console job")
OFFLINE_IN_JOB = ("team config: --offline was passed inside a console job (TC_API_URL, TC_JOB_TOKEN and TC_PROJECT "
                  "are set): {what} refuses to fall back to project.yml's sprint.freeze_days, which the PR's own "
                  "branch controls (ADR 0009 d.11, threat model T10); drop --offline so it reads the console's "
                  "team config instead")
_PROBLEM_TYPE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
# The variables `console.config` names in its own refusals; a console answer's `field` is never shown.
_OWN_FIELDS = frozenset(JOB_ENV + ("TC_RUNNER_SOCKET", "TC_CONSOLE_URL"))


def cause(exc: "console.ConsoleError") -> str:
    """`<status> <type>` of a refused call, the type allow-listed: a console problem body is data, and a type that is
    not a plain slug (a newline, a forged line) is shown as `problem`, never quoted."""
    kind = exc.type if _PROBLEM_TYPE.match(exc.type or "") else "problem"
    return " ".join(p for p in (str(exc.status) if exc.status else "", kind) if p)


def refusal(exc: Exception, what: str) -> str:
    """The reason line for a run that cannot act on the team config, always one line. Never the console's own text:
    the status and an allow-listed problem type, the variable the plugin itself found missing, or the path its check
    refused (printable ASCII, see `_path`)."""
    if isinstance(exc, console.ConsoleError):
        own = exc.status is None and exc.type.startswith("config-") and exc.field in _OWN_FIELDS
        return (f"team config: the console's resolved view could not be read ({cause(exc)}"
                f"{f', {exc.field}' if own else ''}): {what} fails closed and never falls back to project.yml")
    return (f"team config: the console's resolved view is not one this plugin can act on ({exc}): {what} fails "
            "closed and never falls back to project.yml")


def summary(view: Mapping[str, Any], drift: List[str]) -> Dict[str, Any]:
    """What a command reports about the config it acted on."""
    return {"source": "console", "revision": view["revision"], "disabled_roles": sorted(disabled_roles(view)),
            "drift": drift}


def disabled_roles(view: Mapping[str, Any]) -> FrozenSet[str]:
    return frozenset(role for role, entry in view["roles"].items() if entry["enabled"] is False)


def role_of_agent(name: str) -> Optional[str]:
    """The console role an agent runs as; None for a product's own specialist, which the console does not know."""
    role = AGENT_ROLES.get(name, name)
    return role if role in TEAM_ROLES else None


def review_policy(view: Mapping[str, Any]) -> Dict[str, Any]:
    """The gate policy from `gates`, in the shape `review.required_verdicts` reads. A glob the matcher does not
    support is a refusal: read literally it would match nothing and drop a required review."""
    gates = view["gates"]
    for index, glob in enumerate(gates["code_paths"]):
        try:
            review.glob_regex(glob)
        except ValueError:
            raise TeamConfigInvalid(f"gates.code_paths[{index}]", "uses glob syntax pr gate does not support "
                                    "({a,b}, [ab] or !negation)") from None
    return {"configured": True, "source": f"team config (console, revision {view['revision']})",
            "revision": view["revision"], "qa": gates["qa"], "reviewer": gates["reviewer"],
            "code_paths": list(gates["code_paths"]), "max_rework_rounds": gates["max_rework_rounds"]}


def review_context(view: Optional[Mapping[str, Any]], role: str) -> Tuple[str, str]:
    """(`diff` | `repo`, why) for a reviewing agent. Diff-only is the reviewer's cost lever and nothing else's: QA
    and security always read the code around the change (threat model T11), and a lever applies only under
    `api_key` (ADR 0009 d.5)."""
    if role != "reviewer":
        return "repo", f"{role} always reads the code around the change: diff-only review is for the reviewer only"
    if view is None:
        return "repo", "no team config for this run: full context"
    if view["cost"]["reviewer_context"] != "diff":
        return "repo", f"team config revision {view['revision']}: cost.reviewer_context is repo"
    if view["access"]["source"] != "api_key":
        return "repo", (f"team config revision {view['revision']}: cost levers apply only under api_key, this "
                        f"project runs on {view['access']['source']}")
    return "diff", f"team config revision {view['revision']}: cost.reviewer_context is diff"


def drift(view: Mapping[str, Any], settings: Mapping[str, Any]) -> List[str]:
    """The `project.yml` keys (`project.imported_settings_from_text`) whose value differs from the console's view.
    Reported, never acted on: on a console-tracker project those copies are documentation."""
    console_values: Dict[str, Any] = {f"review.{k}": view["gates"][k] for k in
                                      ("qa", "reviewer", "code_paths", "max_rework_rounds")}
    console_values.update({f"caps.{mode}.{k}": v for mode, cap in view["caps"].items() for k, v in cap.items()})
    console_values.update({f"sprint.{k}": v for k, v in view["sprint"].items()})
    console_values["owner.language"] = view["language"]
    differing = []
    for key, value in settings.items():
        if key not in console_values:
            differing.append(key)  # unreadable or unknown in project.yml: it cannot agree with the console
        elif value != console_values[key]:
            differing.append(key)
    return sorted(differing)


def drift_warning(view: Mapping[str, Any], keys: List[str]) -> str:
    return (f"team config: project.yml differs from the console's team config (revision {view['revision']}) at "
            f"{', '.join(keys[:20])}{f' (+{len(keys) - 20} more)' if len(keys) > 20 else ''}; the console's values "
            "apply, project.yml's review, caps, sprint and owner.language are import-only on a console-tracker "
            "project")
