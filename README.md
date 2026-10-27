# Ansible runner

A [labdex](https://github.com/labdex-app/labdex) plugin. Runs the playbooks and roles of each host's features, with the feature tree's merged variables. Plan = --check --diff, apply = the real run. Also runs single commands and package installs on a host.

**Version** 1.1 · **Works with** labdex >=1.3,<2 · **Plugin id** `ansible`

> [!WARNING]
> Built with AI, for home labs only: labdex and its plugins were written largely with an AI
> coding assistant (Claude), directed and reviewed by a human. They haven't had an independent
> security audit and may contain security problems. Run them on a trusted home network.

## Install

It ships with labdex: **Plugins › Marketplace**, then *Install* (nothing is downloaded). Updates arrive through the official Marketplace (signed with the labdex project key).

## Settings

| Setting | Notes |
| --- | --- |
| Playbooks come from |  |
| Folder | Folder with playbooks (and roles/ for roles). For git: where the checkout goes; empty = inside labdex's data folder. |
| Git repository | e.g. git@github.com:me/homelab-ansible.git |
| Branch |  |
| Git deploy key | Read-only deploy key for the repository |
| SSH user |  |
| SSH private key | Pick a global secret to share one key between modules |
| Connection |  |
| Use sudo |  |
| Check SSH host keys |  |
| Ansible Vault password | For files encrypted with ansible-vault in the playbooks. Pick a global secret to share it. |
| Vault id | Only if your files use a vault id (ansible-vault --vault-id NAME@…) |
| Plan with --check --diff | Turn off for playbooks that can't run in check mode; the plan then only lists what would run |

## Actions

- **Check setup** (run): Checks ansible-playbook and that every playbook the features name exists
- **Pull repository** (run): git clone / pull (source: git)
- **Apply features** (plan): Every host's features, ancestors first. Optionally only one branch of the tree.
- **Apply this host's features** (plan)
- **Run a command** (run)
- **Install packages** (run)

## Releasing

Bump `version` in `plugin.yaml`, add a `## <version>` section to `CHANGELOG.md`, commit, then
publish a GitHub release tagged `v<version>` (web UI, or `gh release create v<version> --target main --notes ""`).
The release workflow signs the plugin with the `LABDEX_SIGNING_KEY` secret, attaches the zip and
`CHANGELOG.md` to the release and puts that version's CHANGELOG section into its description.
Files listed in `.releaseignore` stay out of the zip.
