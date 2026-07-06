#!/usr/local/bin/python
import grp
import os
import pwd
import sys


def read_id(name: str, default: int) -> int:
    value = os.environ.get(name, str(default)).strip()
    try:
        parsed = int(value)
    except ValueError:
        print(f"{name} must be an integer, got {value!r}.", file=sys.stderr)
        sys.exit(2)
    if parsed < 0:
        print(f"{name} must be zero or greater, got {parsed}.", file=sys.stderr)
        sys.exit(2)
    return parsed


def unique_name(base: str, numeric_id: int, exists) -> str:
    candidates = [base, f"{base}{numeric_id}"]
    candidates.extend(f"{base}{numeric_id}_{index}" for index in range(1, 100))
    for candidate in candidates:
        try:
            exists(candidate)
        except KeyError:
            return candidate
    print(f"Could not allocate a unique account name for id {numeric_id}.", file=sys.stderr)
    sys.exit(2)


def ensure_group(gid: int) -> str:
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        name = unique_name("webdocs", gid, grp.getgrnam)
        with open("/etc/group", "a", encoding="utf-8") as group_file:
            group_file.write(f"{name}:x:{gid}:\n")
        return name


def ensure_user(uid: int, gid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        name = unique_name("webdocs", uid, pwd.getpwnam)
        with open("/etc/passwd", "a", encoding="utf-8") as passwd_file:
            passwd_file.write(f"{name}:x:{uid}:{gid}:Web Docs:/tmp:/usr/sbin/nologin\n")
        return name


def drop_privileges(uid: int, gid: int) -> None:
    os.setgroups([gid])
    os.setgid(gid)
    os.setuid(uid)


def main() -> None:
    if len(sys.argv) < 2:
        print("No command provided.", file=sys.stderr)
        sys.exit(2)

    if os.geteuid() == 0:
        uid = read_id("WEB_DOCS_UID", 1000)
        gid = read_id("WEB_DOCS_GID", 1000)
        ensure_group(gid)
        user_name = ensure_user(uid, gid)
        os.environ["WEB_DOCS_UID"] = str(uid)
        os.environ["WEB_DOCS_GID"] = str(gid)
        os.environ["HOME"] = os.environ.get("HOME", "/tmp") or "/tmp"
        os.environ["LOGNAME"] = user_name
        os.environ["USER"] = user_name
        drop_privileges(uid, gid)

    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
