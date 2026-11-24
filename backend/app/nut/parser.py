"""Structured parsers, validators, and serializers for NUT configuration files."""

from __future__ import annotations

import re
import shlex
from typing import Any

from app.models.schemas import (
    SECRET_MASK,
    NutConfModel,
    NutConfigFile,
    UpsConfModel,
    UpsDefinition,
    UpsDirective,
    UpsdConfModel,
    UpsdListenEntry,
    UpsdUserEntry,
    UpsdUsersModel,
    UpsmonConfModel,
    UpsmonMonitorEntry,
    UpsmonNotifyFlag,
)

_IDENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_CONTROL_CHAR_RE = re.compile(r"[\r\n\x00-\x1f\x7f]")


def _ensure_safe_token(value: str, label: str, allow_spaces: bool = False) -> str:
    if _CONTROL_CHAR_RE.search(value):
        raise ValueError(f"{label} contains invalid control characters or newlines")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{label} must not be empty")
    if not allow_spaces and any(ch.isspace() for ch in cleaned):
        raise ValueError(f"{label} must not contain whitespace")
    return cleaned


def _ensure_ident(value: str, label: str) -> str:
    cleaned = _ensure_safe_token(value, label, allow_spaces=False)
    if not _IDENT_RE.match(cleaned):
        raise ValueError(f"{label} '{cleaned}' may only contain letters, digits, '_', '-', and '.'")
    return cleaned


def _ensure_safe_value(value: str, label: str) -> str:
    if _CONTROL_CHAR_RE.search(value):
        raise ValueError(f"{label} contains invalid control characters or newlines")
    return value.strip()


def _format_ini_value(value: str) -> str:
    val = value.strip()
    if not val:
        return '""'
    if any(ch.isspace() or ch in '#;"' for ch in val):
        escaped = val.replace('"', '\\"')
        return f'"{escaped}"'
    return val


def _strip_inline_comment(line: str) -> str:
    """Strip # comments outside quotes."""
    in_quote: str | None = None
    escaped = False
    for idx, ch in enumerate(line):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch in ('"', "'"):
            if in_quote is None:
                in_quote = ch
            elif in_quote == ch:
                in_quote = None
            continue
        if ch == "#" and in_quote is None:
            return line[:idx].strip()
    return line.strip()


def _unquote(val: str) -> str:
    v = val.strip()
    if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
        inner = v[1:-1]
        return inner.replace('\\"', '"')
    return v


# --- nut.conf ---

def parse_nut_conf(content: str) -> NutConfModel:
    mode = "netserver"
    extra: list[str] = []
    for raw_line in content.splitlines():
        line = _strip_inline_comment(raw_line)
        if not line:
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            if k.strip().upper() == "MODE":
                candidate = _unquote(v).lower()
                if candidate in ("none", "standalone", "netserver", "netclient"):
                    mode = candidate
                continue
        extra.append(line)
    return NutConfModel(mode=mode, extra_lines=extra)  # type: ignore[arg-type]


def serialize_nut_conf(model: NutConfModel) -> str:
    lines = ["# Managed by WEBNUT", f"MODE={model.mode}"]
    for extra in model.extra_lines:
        cleaned = _ensure_safe_value(extra, "nut.conf extra line")
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines) + "\n"


# --- ups.conf ---

def parse_ups_conf(content: str) -> UpsConfModel:
    global_directives: list[UpsDirective] = []
    ups_list: list[UpsDefinition] = []
    current_section: dict[str, Any] | None = None

    def flush_section() -> None:
        if current_section is None:
            return
        directives: list[UpsDirective] = []
        driver = ""
        port = "auto"
        desc = ""
        for k, v in current_section["items"]:
            kl = k.lower()
            if kl == "driver":
                driver = v
            elif kl == "port":
                port = v
            elif kl == "desc":
                desc = v
            else:
                directives.append(UpsDirective(key=k, value=v))
        ups_list.append(
            UpsDefinition(
                name=current_section["name"],
                driver=driver or "usbhid-ups",
                port=port or "auto",
                desc=desc,
                directives=directives,
            )
        )

    for raw_line in content.splitlines():
        line = _strip_inline_comment(raw_line)
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            flush_section()
            current_section = {"name": line[1:-1].strip(), "items": []}
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            key = k.strip()
            val = _unquote(v)
        else:
            key = line.strip()
            val = ""
        if current_section is None:
            global_directives.append(UpsDirective(key=key, value=val))
        else:
            current_section["items"].append((key, val))

    flush_section()
    return UpsConfModel(global_directives=global_directives, ups_list=ups_list)


def serialize_ups_conf(model: UpsConfModel) -> str:
    lines = ["# Managed by WEBNUT"]
    for d in model.global_directives:
        key = _ensure_ident(d.key, "ups.conf global directive key")
        val = _ensure_safe_value(d.value, f"ups.conf directive '{key}'")
        if val:
            lines.append(f"{key} = {_format_ini_value(val)}")
        else:
            lines.append(key)

    seen_names: set[str] = set()
    for ups in model.ups_list:
        name = _ensure_ident(ups.name, "UPS name")
        if name in seen_names:
            raise ValueError(f"Duplicate UPS section name '{name}'")
        seen_names.add(name)
        driver = _ensure_ident(ups.driver, f"UPS '{name}' driver")
        port = _ensure_safe_token(ups.port, f"UPS '{name}' port", allow_spaces=False)
        lines.append("")
        lines.append(f"[{name}]")
        lines.append(f"  driver = {_format_ini_value(driver)}")
        lines.append(f"  port = {_format_ini_value(port)}")
        if ups.desc.strip():
            desc = _ensure_safe_value(ups.desc, f"UPS '{name}' desc")
            lines.append(f"  desc = {_format_ini_value(desc)}")
        for d in ups.directives:
            key = _ensure_ident(d.key, f"UPS '{name}' directive key")
            if key.lower() in ("driver", "port", "desc"):
                continue
            val = _ensure_safe_value(d.value, f"UPS '{name}' directive '{key}'")
            if val:
                lines.append(f"  {key} = {_format_ini_value(val)}")
            else:
                lines.append(f"  {key}")

    return "\n".join(lines) + "\n"


# --- upsd.conf ---

def parse_upsd_conf(content: str) -> UpsdConfModel:
    listens: list[UpsdListenEntry] = []
    maxage: int | None = None
    maxconn: int | None = None
    statepath: str | None = None
    extra: list[str] = []

    for raw_line in content.splitlines():
        line = _strip_inline_comment(raw_line)
        if not line:
            continue
        try:
            parts = shlex.split(line)
        except ValueError:
            parts = line.split()
        if not parts:
            continue
        keyword = parts[0].upper()
        if keyword == "LISTEN" and len(parts) >= 2:
            addr = parts[1]
            port = 3493
            if len(parts) >= 3:
                try:
                    port = int(parts[2])
                except ValueError:
                    port = 3493
            listens.append(UpsdListenEntry(address=addr, port=port))
        elif keyword == "MAXAGE" and len(parts) >= 2:
            try:
                maxage = int(parts[1])
            except ValueError:
                extra.append(line)
        elif keyword == "MAXCONN" and len(parts) >= 2:
            try:
                maxconn = int(parts[1])
            except ValueError:
                extra.append(line)
        elif keyword == "STATEPATH" and len(parts) >= 2:
            statepath = parts[1]
        else:
            extra.append(line)

    return UpsdConfModel(
        listens=listens,
        maxage=maxage,
        maxconn=maxconn,
        statepath=statepath,
        extra_lines=extra,
    )


def serialize_upsd_conf(model: UpsdConfModel) -> str:
    lines = ["# Managed by WEBNUT"]
    for entry in model.listens:
        addr = _ensure_safe_token(entry.address, "LISTEN address", allow_spaces=False)
        lines.append(f"LISTEN {addr} {entry.port}")
    if model.maxage is not None:
        lines.append(f"MAXAGE {model.maxage}")
    if model.maxconn is not None:
        lines.append(f"MAXCONN {model.maxconn}")
    if model.statepath:
        sp = _ensure_safe_token(model.statepath, "STATEPATH", allow_spaces=False)
        lines.append(f"STATEPATH {sp}")
    for extra in model.extra_lines:
        cleaned = _ensure_safe_value(extra, "upsd.conf extra line")
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines) + "\n"


# --- upsd.users ---

def parse_upsd_users(content: str, mask_secrets: bool = False) -> UpsdUsersModel:
    users: list[UpsdUserEntry] = []
    current: UpsdUserEntry | None = None

    for raw_line in content.splitlines():
        line = _strip_inline_comment(raw_line)
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            if current is not None:
                users.append(current)
            current = UpsdUserEntry(username=line[1:-1].strip())
            continue
        if current is None:
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            key = k.strip().lower()
            val = _unquote(v)
            if key == "password":
                current.password = SECRET_MASK if (mask_secrets and val) else val
            elif key == "actions":
                current.actions.extend(tok for tok in val.split() if tok)
            elif key == "instcmds":
                current.instcmds.extend(tok for tok in val.split() if tok)
            elif key == "upsmon":
                role = val.strip().lower()
                if role == "master":
                    role = "primary"
                elif role == "slave":
                    role = "secondary"
                if role in ("primary", "secondary"):
                    current.upsmon_role = role  # type: ignore[assignment]
        else:
            parts = line.split()
            if len(parts) >= 2 and parts[0].lower() == "upsmon":
                role = parts[1].lower()
                if role == "master":
                    role = "primary"
                elif role == "slave":
                    role = "secondary"
                if role in ("primary", "secondary"):
                    current.upsmon_role = role  # type: ignore[assignment]

    if current is not None:
        users.append(current)
    return UpsdUsersModel(users=users)


def serialize_upsd_users(model: UpsdUsersModel) -> str:
    lines = ["# Managed by WEBNUT"]
    seen: set[str] = set()
    for user in model.users:
        username = _ensure_ident(user.username, "upsd.users username")
        if username in seen:
            raise ValueError(f"Duplicate NUT user '{username}'")
        seen.add(username)
        lines.append("")
        lines.append(f"[{username}]")
        if user.password:
            pw = _ensure_safe_value(user.password, f"User '{username}' password")
            lines.append(f"  password = {_format_ini_value(pw)}")
        for action in user.actions:
            act = _ensure_ident(action, f"User '{username}' action")
            lines.append(f"  actions = {act}")
        for cmd in user.instcmds:
            inst = _ensure_safe_token(cmd, f"User '{username}' instcmd", allow_spaces=False)
            lines.append(f"  instcmds = {inst}")
        if user.upsmon_role in ("primary", "secondary"):
            lines.append(f"  upsmon {user.upsmon_role}")
    return "\n".join(lines) + "\n"


# --- upsmon.conf ---

def parse_upsmon_conf(content: str, mask_secrets: bool = False) -> UpsmonConfModel:
    model = UpsmonConfModel()
    for raw_line in content.splitlines():
        line = _strip_inline_comment(raw_line)
        if not line:
            continue
        try:
            parts = shlex.split(line)
        except ValueError:
            parts = line.split()
        if not parts:
            continue
        kw = parts[0].upper()
        if kw == "RUN_AS_USER" and len(parts) >= 2:
            model.run_as_user = parts[1]
        elif kw == "MINSUPPLIES" and len(parts) >= 2:
            model.minsupplies = int(parts[1])
        elif kw == "SHUTDOWNCMD" and len(parts) >= 2:
            model.shutdowncmd = parts[1]
        elif kw == "NOTIFYCMD" and len(parts) >= 2:
            model.notifycmd = parts[1]
        elif kw == "POLLFREQ" and len(parts) >= 2:
            model.pollfreq = int(parts[1])
        elif kw == "POLLFREQALERT" and len(parts) >= 2:
            model.pollfreqalert = int(parts[1])
        elif kw == "HOSTSYNC" and len(parts) >= 2:
            model.hostsync = int(parts[1])
        elif kw == "DEADTIME" and len(parts) >= 2:
            model.deadtime = int(parts[1])
        elif kw == "FINALDELAY" and len(parts) >= 2:
            model.finaldelay = int(parts[1])
        elif kw == "POWERDOWNFLAG" and len(parts) >= 2:
            model.powerdownflag = parts[1]
        elif kw == "MONITOR" and len(parts) >= 6:
            role = parts[5].lower()
            if role == "master":
                role = "primary"
            elif role == "slave":
                role = "secondary"
            if role not in ("primary", "secondary"):
                role = "primary"
            pw = SECRET_MASK if (mask_secrets and parts[4]) else parts[4]
            model.monitors.append(
                UpsmonMonitorEntry(
                    ups_spec=parts[1],
                    power_value=int(parts[2]),
                    username=parts[3],
                    password=pw,
                    role=role,  # type: ignore[arg-type]
                )
            )
        elif kw == "NOTIFYFLAG" and len(parts) >= 3:
            model.notifyflags.append(
                UpsmonNotifyFlag(event=parts[1].upper(), flags=parts[2].upper())
            )
        else:
            model.extra_lines.append(line)
    return model


def serialize_upsmon_conf(model: UpsmonConfModel) -> str:
    lines = ["# Managed by WEBNUT"]
    run_as = _ensure_ident(model.run_as_user, "RUN_AS_USER")
    lines.append(f"RUN_AS_USER {run_as}")
    lines.append(f"MINSUPPLIES {model.minsupplies}")
    shutdowncmd = _ensure_safe_value(model.shutdowncmd or "/bin/true", "SHUTDOWNCMD")
    lines.append(f'SHUTDOWNCMD "{shutdowncmd.replace(chr(34), chr(92) + chr(34))}"')
    if model.notifycmd.strip():
        notifycmd = _ensure_safe_value(model.notifycmd, "NOTIFYCMD")
        lines.append(f'NOTIFYCMD "{notifycmd.replace(chr(34), chr(92) + chr(34))}"')
    lines.append(f"POLLFREQ {model.pollfreq}")
    lines.append(f"POLLFREQALERT {model.pollfreqalert}")
    lines.append(f"HOSTSYNC {model.hostsync}")
    lines.append(f"DEADTIME {model.deadtime}")
    lines.append(f"FINALDELAY {model.finaldelay}")
    pflag = _ensure_safe_token(model.powerdownflag or "/etc/killpower", "POWERDOWNFLAG", allow_spaces=False)
    lines.append(f"POWERDOWNFLAG {pflag}")

    for mon in model.monitors:
        spec = _ensure_safe_token(mon.ups_spec, "MONITOR ups_spec", allow_spaces=False)
        user = _ensure_ident(mon.username, "MONITOR username")
        pw = _ensure_safe_token(mon.password, "MONITOR password", allow_spaces=False)
        lines.append(f"MONITOR {spec} {mon.power_value} {user} {pw} {mon.role}")

    for nf in model.notifyflags:
        ev = _ensure_ident(nf.event.upper(), "NOTIFYFLAG event")
        fl = _ensure_safe_token(nf.flags.upper(), "NOTIFYFLAG flags", allow_spaces=False)
        lines.append(f"NOTIFYFLAG {ev} {fl}")

    for extra in model.extra_lines:
        cleaned = _ensure_safe_value(extra, "upsmon.conf extra line")
        if cleaned:
            lines.append(cleaned)

    return "\n".join(lines) + "\n"


# --- Unified Dispatch Helpers ---

def parse_config_file(
    filename: NutConfigFile,
    content: str,
    mask_secrets: bool = True,
) -> dict[str, Any]:
    if filename == "nut.conf":
        return parse_nut_conf(content).model_dump()
    if filename == "ups.conf":
        return parse_ups_conf(content).model_dump()
    if filename == "upsd.conf":
        return parse_upsd_conf(content).model_dump()
    if filename == "upsd.users":
        return parse_upsd_users(content, mask_secrets=mask_secrets).model_dump()
    if filename == "upsmon.conf":
        return parse_upsmon_conf(content, mask_secrets=mask_secrets).model_dump()
    raise ValueError(f"Unsupported NUT config file: {filename}")


def validate_and_serialize(filename: NutConfigFile, data: dict[str, Any]) -> str:
    if filename == "nut.conf":
        return serialize_nut_conf(NutConfModel.model_validate(data))
    if filename == "ups.conf":
        return serialize_ups_conf(UpsConfModel.model_validate(data))
    if filename == "upsd.conf":
        return serialize_upsd_conf(UpsdConfModel.model_validate(data))
    if filename == "upsd.users":
        return serialize_upsd_users(UpsdUsersModel.model_validate(data))
    if filename == "upsmon.conf":
        return serialize_upsmon_conf(UpsmonConfModel.model_validate(data))
    raise ValueError(f"Unsupported NUT config file: {filename}")


def merge_masked_secrets(
    filename: NutConfigFile,
    incoming: dict[str, Any],
    existing_raw: str,
) -> dict[str, Any]:
    """Replace SECRET_MASK sentinel values in incoming payload with existing passwords."""
    merged = dict(incoming)
    if filename == "upsd.users":
        existing = parse_upsd_users(existing_raw, mask_secrets=False)
        pw_map = {u.username: u.password for u in existing.users}
        users = []
        for u in merged.get("users", []):
            item = dict(u)
            if item.get("password") == SECRET_MASK:
                item["password"] = pw_map.get(item.get("username", ""), "")
            users.append(item)
        merged["users"] = users
    elif filename == "upsmon.conf":
        existing_mon = parse_upsmon_conf(existing_raw, mask_secrets=False)
        pw_map = {(m.ups_spec, m.username): m.password for m in existing_mon.monitors}
        monitors = []
        for m in merged.get("monitors", []):
            item = dict(m)
            if item.get("password") == SECRET_MASK:
                item["password"] = pw_map.get(
                    (item.get("ups_spec", ""), item.get("username", "")), ""
                )
            monitors.append(item)
        merged["monitors"] = monitors
    return merged
