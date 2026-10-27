"""Ansible runner for the feature tree.

For each feature path a host has (ancestors included: kube → kube.cluster →
kube.cluster.oak), the feature's playbooks run against the hosts that have it,
then its roles (wrapped in a generated play). Variables come from the
inventory: feature vars merged root → leaf, then the host's own vars.
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from labdex.features import chain, expanded
from labdex.plugins.base import Change, Plugin as Base, PluginError


class Plugin(Base):
    # ---------------------------------------------------------------- source
    @property
    def source_dir(self) -> Path:
        s = self.settings
        if s.get("source") == "git":
            return Path(s.get("path") or (self.ctx.data_dir / "ansible-src" / self.ctx.name))
        p = Path(s.get("path") or "")
        if not str(p):
            raise PluginError("set the playbook folder")
        return p

    def _require_source(self) -> Path:
        d = self.source_dir
        if not d.is_dir():
            hint = "run 'Pull repository' first" if self.settings.get("source") == "git" else "mount it into the container"
            raise PluginError(f"{d} doesn't exist; {hint}")
        return d

    def run_pull(self, params):
        s = self.settings
        if s.get("source") != "git":
            raise PluginError("the source is a folder; nothing to pull")
        if not s.get("repo_url"):
            raise PluginError("set the git repository")
        git = shutil.which("git")
        if git is None:
            raise PluginError("git isn't installed")
        dest = self.source_dir
        with tempfile.TemporaryDirectory(prefix="labdex-git-") as tmp:
            env = {**os.environ, "HOME": tmp, "GIT_TERMINAL_PROMPT": "0"}
            if s.get("deploy_key"):
                key = Path(tmp) / "key"
                fd = os.open(key, os.O_WRONLY | os.O_CREAT, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(s["deploy_key"].strip() + "\n")
                env["GIT_SSH_COMMAND"] = f"ssh -i {key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile={tmp}/known_hosts"
            branch = s.get("branch") or "main"
            if (dest / ".git").is_dir():
                cmds = [[git, "-C", str(dest), "fetch", "--depth", "1", "origin", branch],
                        [git, "-C", str(dest), "reset", "--hard", "FETCH_HEAD"]]
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                cmds = [[git, "clone", "--depth", "1", "--branch", branch, s["repo_url"], str(dest)]]
            for cmd in cmds:
                self.log("$ " + " ".join(cmd[1:]))
                p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300)
                for line in (p.stdout + p.stderr).splitlines():
                    self.log(line)
                if p.returncode:
                    raise PluginError(f"git failed ({p.returncode})")
            head = subprocess.run([git, "-C", str(dest), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, env=env)
        return {"message": f"{s['repo_url']} @ {branch} {head.stdout.strip()}"}

    def test(self):
        if shutil.which("ansible-playbook") is None:
            raise PluginError("ansible-playbook isn't installed in this labdex instance (build the image with ANSIBLE=1)")
        d = self._require_source()
        feats = self.ctx.inventory.features()
        missing = [f"{p}: {pb}" for p, f in feats.items() for pb in f["playbooks"] if not (d / pb).is_file()]
        missing += [f"{p}: role {r}" for p, f in feats.items() for r in f["roles"] if not (d / "roles" / r).is_dir()]
        if missing:
            raise PluginError("not found in the source: " + "; ".join(missing[:10]))
        return f"OK: {len(feats)} features, source {d}"

    # ------------------------------------------------------------ inventory
    def _inventory(self, hosts: list[dict]) -> dict:
        inv = self.ctx.inventory.ansible_inventory()
        keep = {h["name"] for h in hosts}
        hv = inv["all"]["hosts"]
        for name in list(hv):
            if name not in keep:
                del hv[name]
        for h in hosts:
            ms = h.get("module_settings") or {}
            if ms.get("ssh_user"):
                hv[h["name"]]["ansible_user"] = ms["ssh_user"]
            if ms.get("port"):
                hv[h["name"]]["ansible_port"] = ms["port"]
        for g in inv["all"]["children"].values():
            g["hosts"] = {n: None for n in g["hosts"] if n in keep}
        return inv

    # ---------------------------------------------------------------- steps
    def steps(self, hosts: list[dict], only: str = "") -> list[dict]:
        """[{feature, kind: playbook|roles, ref, hosts}] in run order."""
        feats = self.ctx.inventory.features()
        if only and only not in feats:
            raise PluginError(f"no feature '{only}'")
        per_host = {}
        for h in hosts:
            paths = expanded(h.get("features") or [])
            if only:
                if not any(p == only or p.startswith(only + ".") for p in paths):
                    continue
                line = set(chain(only))
                paths = [p for p in paths if p in line or p.startswith(only + ".")]
            per_host[h["name"]] = paths
        order = []
        for paths in per_host.values():
            for p in paths:
                if p not in order:
                    order.append(p)
        # first appearance keeps every ancestor before its descendants (expanded() is ancestors-first)
        out = []
        for p in order:
            f = feats.get(p)
            if f is None:
                continue  # an implied parent that isn't defined
            targets = sorted(n for n, paths in per_host.items() if p in paths)
            for pb in f["playbooks"]:
                out.append({"feature": p, "kind": "playbook", "ref": pb, "hosts": targets})
            if f["roles"]:
                out.append({"feature": p, "kind": "roles", "ref": ", ".join(f["roles"]), "roles": f["roles"], "hosts": targets})
        return out

    def _run_step(self, step, inv, src, check):
        kw = {"inventory_override": inv, "limit": ",".join(step["hosts"]), "base_dir": src,
              "roles_path": src / "roles", "check": check, "diff": True}
        if step["kind"] == "playbook":
            return self.ctx.ansible(step["ref"], **kw)
        play = [{"name": f"labdex feature {step['feature']}", "hosts": "all", "become": bool(self.settings.get("become", True)),
                 "roles": step["roles"]}]
        return self.ctx.ansible(inline=play, **kw)

    def _plan(self, hosts, only=""):
        src = self._require_source()
        steps = self.steps(hosts, only)
        if not steps:
            self.log("no host has a feature with playbooks or roles")
            return []
        inv = self._inventory(hosts)
        changes, total = [], 0
        for st in steps:
            label = f"{st['feature']}: {st['ref']}"
            if self.settings.get("plan_with_check", True):
                self.log(f"--- check {label} on {', '.join(st['hosts'])}")
                res = self._run_step(st, inv, src, check=True)
                if not res.ok:
                    raise PluginError(f"check of {label} failed (rc={res.rc}, failed={res.failed}, unreachable={res.unreachable})")
                total += res.changed
                summary = f"{res.changed} task(s) would change on {len(st['hosts'])} host(s)"
            else:
                summary = f"runs on {len(st['hosts'])} host(s) (not checked)"
                total += 1
            changes.append(Change("ansible", st["kind"], label, summary, None, {"hosts": st["hosts"]}, {"step": st}))
        if self.settings.get("plan_with_check", True) and total == 0:
            self.log("check mode reports nothing to change")
            return []
        return changes

    def _apply(self, changes, hosts):
        src = self._require_source()
        inv = self._inventory(hosts)
        changed = 0
        for c in changes:
            st = c.data["step"]
            self.log(f"--- run {c.name} on {', '.join(st['hosts'])}")
            res = self._run_step(st, inv, src, check=False)
            if not res.ok:
                raise PluginError(f"{c.name} failed (rc={res.rc}, failed={res.failed}, unreachable={res.unreachable}); later steps didn't run")
            changed += res.changed
        return {"changed": changed, "steps": len(changes)}

    def _machines(self) -> list[dict]:
        """VIPs are addresses, not machines: Ansible never targets them (their members get labdex_vips)."""
        return [h for h in self.ctx.hosts() if h.get("kind") != "vip"]

    def plan_apply_features(self, params):
        return self._plan(self._machines(), (params.get("feature") or "").strip())

    def apply_apply_features(self, changes, params):
        return self._apply(changes, self._machines())

    def _one(self, params) -> list[dict]:
        h = self.ctx.host(params["host"])
        if h is None:
            raise PluginError(f"{params['host']} isn't enabled for {self.ctx.name}")
        if h.get("kind") == "vip":
            raise PluginError(f"{h['name']} is a VIP; run this on one of its members ({', '.join(h.get('vip_members') or []) or 'none yet'})")
        return [h]

    def plan_host_features(self, params):
        return self._plan(self._one(params))

    def apply_host_features(self, changes, params):
        return self._apply(changes, self._one(params))

    # ------------------------------------------------------------- ad hoc
    def _adhoc(self, params, tasks, become):
        hosts = self._one(params)
        src = self.source_dir if self.source_dir.is_dir() else self.ctx.plugin_dir
        play = [{"name": "labdex ad-hoc", "hosts": "all", "gather_facts": False, "become": become, "tasks": tasks}]
        res = self.ctx.ansible(inline=play, inventory_override=self._inventory(hosts), limit=params["host"], base_dir=src)
        if not res.ok:
            raise PluginError(f"failed (rc={res.rc}, failed={res.failed}, unreachable={res.unreachable})")
        return {"changed": res.changed}

    def run_command(self, params):
        tasks = [{"name": "command", "ansible.builtin.shell": params["command"], "register": "out"},
                 {"name": "output", "ansible.builtin.debug": {"var": "out.stdout_lines"}}]
        return self._adhoc(params, tasks, bool(params.get("become")))

    def run_install(self, params):
        tasks = [{"name": "install", "ansible.builtin.package": {"name": params["packages"], "state": "present"}}]
        return self._adhoc(params, tasks, True)
