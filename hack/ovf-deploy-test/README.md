# OVF Deploy Test

Tests OVF/OVA deployment via VM Service on a Supervisor cluster. Uploads OVFs
to a vSphere Content Library using the vSphere API, then deploys and validates
VMs using `kubectl` over SSH.

## Prerequisites

- Python 3.10+
- Access to a vCenter with a Supervisor cluster
- A namespace named `ovftest` with a Content Library named `ovftest` already
  created and bound to the namespace
- Network access to the OVF source URLs (or local file paths)

Install dependencies:

```bash
pip install -r requirements.txt
```

## Commands

The tool has four subcommands: `discover`, `setup`, `deploy`, and `validate`.

### `discover` — Find OVFs from Artifactory

Crawls an Artifactory repository and writes a CSV of all discovered OVF/OVA
files, ready to pass to `deploy`.

```bash
python ovf_deploy_test.py discover ovfs.csv
```


```
usage: ovf_deploy_test.py discover [-h] [--base-url URL] output

positional arguments:
  output            Path to write the CSV file (e.g. ovfs.csv)

options:
  --base-url URL    Artifactory base URL to discover from
                    (default: https://packages.vcfd.broadcom.net/ui/native/cls-generic-virtual/testdata/)
```

### `setup` — Upload OVFs to a Content Library

Uploads OVFs to a content library without deploying them. Run this before
`deploy` or `validate` to pre-populate the library and isolate upload failures
from test failures.

**Smart re-run behavior:** a state file (`<csv>.setup-state.<vcenter>.<library>.json`)
is written after each entry. The vCenter IP and library name are embedded in
the filename so switching environments automatically gets a fresh state. On
re-run, entries that previously failed with a permanent error (bad OVF, bad
checksum, invalid descriptor, etc.) are skipped automatically. Only transient
failures (503, timeout, connection reset) are retried. Re-run until no
transient `SETUP_FAILED` entries remain, then run `deploy` or `validate`.
Delete the state file to force a full re-run from scratch.

OVFs already present in the content library are reported as `SUCCESS` (goal
achieved — no upload needed).

```bash
# Step 1: upload all OVFs — re-run until only permanent failures remain
python ovf_deploy_test.py setup ovfs.csv \
    --vcenter <vcenter-ip> \
    --vcenter-password <password> \
    --parallel 5

# Step 2: run tests against the pre-populated library
python ovf_deploy_test.py deploy   ovfs.csv --vcenter <vcenter-ip> --vcenter-password <password>
python ovf_deploy_test.py validate ovfs.csv --vcenter <vcenter-ip> --vcenter-password <password>
```

```
usage: ovf_deploy_test.py setup [-h] --vcenter HOST --vcenter-password PASS
                                 [--vcenter-user USER]
                                 [--vcenter-root-password PASS]
                                 [--content-library NAME]
                                 [--parallel N] [--report PATH]
                                 csv

positional arguments:
  csv                       CSV file with OVFs to upload

options:
  --vcenter HOST            vCenter hostname or IP (required)
  --vcenter-password PASS   vCenter API password (required)
  --vcenter-user USER       vCenter username (default: administrator@vsphere.local)
  --vcenter-root-password PASS
                            vCenter root SSH password (default: same as --vcenter-password)
  --content-library NAME    Content library name (default: ovftest)
  --parallel N              Number of OVFs to upload concurrently (default: 1)
  --report PATH             Path to write the HTML report (default: <csv>.with-cl-setup.report.html)
```

### `deploy` — Deploy OVFs from a CSV

Reads a CSV file and deploys each OVF via VM Service. For each entry it:

1. Downloads the OVF descriptor to inspect it (detects vApps, extracts network
   and vApp property definitions)
2. Checks the Content Library for the item (expects `setup` was run first);
   records `SKIPPED` if not found
3. Waits for a `VirtualMachineImage` to appear in the target namespace
4. Creates a `VirtualMachine` CR and waits for it to power on
5. **Always deletes the VM** after the test
6. Records the result and updates the HTML report after each VM

**`--cleanup` mode (self-contained, space-efficient):** uploads the OVF inline,
runs the test, deletes the VM, and deletes the CL item — no dependency on
`setup`. Use this when CL space is limited or for one-off runs.

```bash
# Default (requires setup to have been run first)
python ovf_deploy_test.py deploy ovfs.csv \
    --vcenter <vcenter-ip> \
    --vcenter-password <password>

# Self-contained mode (upload + test + delete per entry)
python ovf_deploy_test.py deploy ovfs.csv \
    --vcenter <vcenter-ip> \
    --vcenter-password <password> \
    --cleanup
```

```
usage: ovf_deploy_test.py deploy [-h] --vcenter HOST --vcenter-password PASS
                                  [--vcenter-user USER]
                                  [--vcenter-root-password PASS]
                                  [--supervisor-root-password PASS]
                                  [--namespace NS] [--content-library NAME]
                                  [--vm-class CLASS] [--storage-class CLASS]
                                  [--network-type {nsx,vds}]
                                  [--cleanup] [--no-cleanup-cl]
                                  [--parallel N] [--report PATH]
                                  csv

positional arguments:
  csv                       CSV file with OVFs to deploy

options:
  --vcenter HOST            vCenter hostname or IP (required)
  --vcenter-password PASS   vCenter API password (required)
  --vcenter-user USER       vCenter username (default: administrator@vsphere.local)
  --vcenter-root-password PASS
                            vCenter root SSH password (default: same as --vcenter-password)
  --supervisor-root-password PASS
                            Supervisor root SSH password (default: retrieved from vCenter)
  --namespace NS            Target namespace (default: ovftest)
  --content-library NAME    Content library name (default: ovftest)
  --vm-class CLASS          VM class to use (default: best-effort-xsmall)
  --storage-class CLASS     Storage class for VM disks (default: wcpglobal-storage-profile)
  --network-type {nsx,vds}  Network type: nsx uses SubnetSet, vds uses Network (default: nsx)
  --cleanup                 Upload OVF inline and delete CL item after test (self-contained mode)
  --no-cleanup-cl           When --cleanup is set, skip deleting the content library item
  --parallel N              Number of OVFs to deploy concurrently (default: 1)
  --report PATH             Path to write the HTML report (default: <csv>.with-vmop.report.html)
```

### `validate` — Validate OVFs directly via vSphere API

Uploads each OVF to a content library and deploys it directly via the vSphere
REST API (`POST /api/vcenter/ovf/library-item/{id}?action=deploy`), bypassing
VM Service entirely. Supports both single-VM OVFs and multi-VM vApps. Useful
for checking whether an OVF is well-formed and deployable against a plain
vCenter cluster. The deployed VM/vApp and content library item are **always
deleted** after each test, regardless of outcome.

The script automatically handles several OVF quirks:
- XML-commented-out file references in OVF descriptors are ignored during upload
- URLs with spaces or non-ASCII characters in paths are percent-encoded before PULL registration
- If the OVF supports DHCP IP allocation, it is requested at deploy time to avoid requiring an IP pool on the target network
- Each deployed VM/vApp gets a unique name suffix to avoid collisions when running with `--parallel`

```bash
python ovf_deploy_test.py validate ovfs.csv \
    --vcenter <vcenter-ip> \
    --vcenter-password <password> \
    --datacenter <datacenter-name> \
    --cluster <cluster-name>
```

```
usage: ovf_deploy_test.py validate [-h] --vcenter HOST --vcenter-password PASS
                                    [--vcenter-user USER]
                                    [--content-library NAME]
                                    [--datacenter NAME] [--cluster NAME]
                                    [--datastore NAME] [--resource-pool NAME]
                                    [--parallel N] [--report PATH]
                                    csv

positional arguments:
  csv                       CSV file with OVFs to validate

options:
  --vcenter HOST            vCenter hostname or IP (required)
  --vcenter-password PASS   vCenter API password (required)
  --vcenter-user USER       vCenter username (default: administrator@vsphere.local)
  --content-library NAME    Content library name for staging (default: ovftest)
  --datacenter NAME         Datacenter to deploy into (default: first available)
  --cluster NAME            Cluster to deploy into (default: first available)
  --datastore NAME          Datastore to deploy into (default: first writable non-vSAN)
  --resource-pool NAME      Resource pool name within the cluster (default: cluster root RP).
                            Required on Supervisor clusters — pass a non-Supervisor child RP
                            (the script prints the full RP tree at startup to help identify it).
  --parallel N              Number of OVFs to validate concurrently (default: 1)
  --report PATH             Path to write the HTML report (default: <csv>.with-cl.report.html)
```

> **Supervisor clusters**: the root resource pool of a Supervisor-enabled cluster
> does not support `importVApp`. Use `--resource-pool` to target a regular child
> resource pool. Run the script once without `--resource-pool` to see the full RP
> tree printed at startup, then re-run with the correct name.

## CSV Format

```
# name,source[,config_file]
my-vm,https://example.com/path/to/vm.ovf
vcsa,/local/path/to/vcsa.ova,vc_vappconfig.yaml
nsx-mgr,https://example.com/nsx.ova,nsx_vappconfig.yaml
```

- **name** — K8s-safe VM name used for the `VirtualMachine` CR and Content
  Library item. Must be lowercase alphanumeric with hyphens.
- **source** — URL (http/https) or absolute local file path to an `.ovf` or
  `.ova` file.
- **config_file** *(optional)* — Path to a YAML file containing
  `spec.bootstrap.vAppConfig.properties` to inject into the VM CR. When
  omitted, the script auto-fills vApp properties with smart defaults (Go
  template expressions for network properties, random passwords for credential
  fields, OVF defaults otherwise).

Lines starting with `#` are comments and are ignored.

## vAppConfig Files

Custom vApp properties are supplied as YAML files with a `properties` key
containing a list of `key`/`value` pairs matching the
`spec.bootstrap.vAppConfig.properties` schema of the `VirtualMachine` CR.

Values may use Go template expressions. The following custom template functions
are available:

| Function | Description |
|---|---|
| `V1alpha6_FormatIP <cidr> <mask>` | Extracts the IP address from a CIDR string. Pass `""` as mask to get a plain IP. |
| `V1alpha6_SubnetMask <cidr>` | Returns the dotted-decimal subnet mask from a CIDR string (e.g. `255.255.255.0`). |
| `V1alpha6_SubnetPrefixLength <cidr>` | Returns the numeric prefix length from a CIDR string (e.g. `24`). |
| `V1alpha6_FormatNameservers <n> <sep>` | Returns nameservers joined by `sep`. Use `-1` for all. |

Example — `vc_vappconfig.yaml` (vCenter appliance):

```yaml
properties:
  - key: guestinfo.cis.appliance.net.addr
    value:
      value: "{{ V1alpha6_FormatIP (index (index .V1alpha6.Net.Devices 0).IPAddresses 0) \"\" }}"
  - key: guestinfo.cis.appliance.net.prefix
    value:
      value: "{{ V1alpha6_SubnetPrefixLength (index (index .V1alpha6.Net.Devices 0).IPAddresses 0) }}"
  - key: guestinfo.cis.appliance.net.gateway
    value:
      value: "{{ (index .V1alpha6.Net.Devices 0).Gateway4 }}"
  - key: guestinfo.cis.appliance.root.passwd
    value:
      value: VMware1!VMware1!
```

See `vc_vappconfig.yaml` and `nsx_vappconfig.yaml` for full examples.

## Report

After each entry is processed, the HTML report is updated incrementally. Open
it in a browser for a summary table with:

- Clickable OVF source links
- Colour-coded status badges: ✅ SUCCESS, ❌ FAILED, 🔧 SETUP_FAILED, ⏭ SKIPPED
- Collapsible vmop controller-manager log excerpts for non-successful entries

### Status meanings

| Status | Meaning |
|---|---|
| `SUCCESS` | VM reached PoweredOn state |
| `FAILED` | VM was created but did not reach PoweredOn within the timeout, or hit a terminal error condition |
| `SETUP_FAILED` | Pre-deployment step failed (Content Library upload error, VMI never appeared, etc.) |
| `SKIPPED` | OVF not deployed: source TLS cert not trusted, multi-VM vApp (deploy only), or not in CL (deploy/validate without `--cleanup`) |

## Hardcoded Defaults

| Setting | Value |
|---|---|
| Storage class | `wcpglobal-storage-profile` (override with `--storage-class`) |
| Default namespace | `ovftest` |
| Default content library | `ovftest` |
| Default VM class | `best-effort-xsmall` |
| Network mapping | NSX: SubnetSet `""`, VDS: Network `""` (set with `--network-type`) |
| VM power-on timeout | 5 minutes |

## How It Works

```
discover          setup                      deploy (default)               validate
────────          ─────                      ────────────────               ────────
Artifactory API   CSV file                   CSV file                       CSV file
    │                 │                          │                              │
    ▼                 ▼                          ▼                              ▼
ovf_files.csv    OvfEntry list             OvfEntry list                 OvfEntry list
                      │                          │                              │
               for each entry:            for each entry:                for each entry:
                      │                          │                              │
               ┌──────▼─────────────┐    ┌───────▼─────────────────┐   ┌──────▼──────────────────────────┐
               │ 1. SUCCESS if item │    │ 1. Download OVF          │   │ 1. Download OVF descriptor       │
               │    already in CL   │    │    descriptor            │   │    (detect vApp)                 │
               │ 2. Upload to CL    │    │ 2. Check CL — SKIPPED    │   │ 2. Upload to CL (no-op if        │
               │    (PULL/PUSH)     │    │    if not found          │   │    setup ran first)              │
               │ 3. Classify error  │    │ 3. Wait for VMI          │   │ 3. Delete any pre-existing VM/   │
               │    transient vs    │    │ 4. Create VM CR          │   │    vApp with the same name       │
               │    permanent       │    │ 5. Poll for PoweredOn    │   │ 4. Deploy VM or vApp via REST    │
               │ 4. Update state    │    │ 6. Always delete VM      │   │ 5. Poll for PoweredOn state      │
               │    file + report   │    │ 7. Record result         │   │ 6. Record result + update report │
               └────────────────────┘    └─────────────────────────┘   │ 7. Always delete VM/vApp + CL    │
                                                                        └──────────────────────────────────┘
               Reports:                  With --cleanup: upload inline,
               <csv>.with-cl-setup       delete CL item after test      Report:
               .report.html              (no setup dependency)          <csv>.with-cl.report.html
               <csv>.setup-state.<vc>.<lib>.json
                                         Report:
                                         <csv>.with-vmop.report.html
```

Upload strategy:
- **Remote URLs** — vCenter performs a PULL transfer directly. The script
  fetches the full TLS certificate chain via `openssl s_client` and passes it
  to vCenter so it can verify the source. If the certificate is expired or
  untrusted, the OVF is marked `SKIPPED`.
- **Local files** — the script streams files to vCenter using chunked PUSH
  transfers, avoiding the 2 GB Python SSL memory limit for large VMDKs.
