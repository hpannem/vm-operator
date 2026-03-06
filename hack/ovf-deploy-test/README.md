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

The tool has two subcommands: `discover` and `deploy`.

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

### `deploy` — Deploy OVFs from a CSV

Reads a CSV file and deploys each OVF one by one. For each entry it:

1. Downloads the OVF descriptor to inspect it (detects vApps, extracts network
   and vApp property definitions)
2. Uploads the OVF to the Content Library (skips if already uploaded with
   non-zero size)
3. Waits for a `VirtualMachineImage` to appear in the target namespace
4. Creates a `VirtualMachine` CR and waits for it to power on
5. Records the result and updates the HTML report after each VM

```bash
python ovf_deploy_test.py deploy ovfs.csv \
    --vcenter <vcenter-ip> \
    --vcenter-password <password>
```

```
usage: ovf_deploy_test.py deploy [-h] --vcenter HOST --vcenter-password PASS
                                  [--vcenter-user USER]
                                  [--vcenter-root-password PASS]
                                  [--supervisor-root-password PASS]
                                  [--namespace NS] [--content-library NAME]
                                  [--vm-class CLASS] [--cleanup] [--report PATH]
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
  --cleanup                 Delete each VM after it is verified
  --report PATH             Path to write the HTML report (default: <csv>.report.html)
```

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

After each VM is processed, the HTML report is updated at
`<csv>.report.html` (or the path given by `--report`). Open it in a browser
for a summary table with:

- Clickable OVF source links
- Colour-coded status badges: ✅ SUCCESS, ❌ FAILED, 🔧 SETUP_FAILED, ⏭ SKIPPED
- Collapsible vmop controller-manager log excerpts for non-successful entries

### Status meanings

| Status | Meaning |
|---|---|
| `SUCCESS` | VM reached PoweredOn state |
| `FAILED` | VM was created but did not reach PoweredOn within the timeout, or hit a terminal error condition |
| `SETUP_FAILED` | Pre-deployment step failed (Content Library upload error, VMI never appeared, etc.) |
| `SKIPPED` | OVF was intentionally not deployed (vApp/multi-VM OVF, or source TLS certificate not trusted by vCenter) |

## Hardcoded Defaults

| Setting | Value |
|---|---|
| Storage class | `wcpglobal-storage-profile` (override with `--storage-class`) |
| Default namespace | `ovftest` |
| Default content library | `ovftest` |
| Default VM class | `best-effort-xsmall` |
| Network mapping | All OVF networks → NSX VPC SubnetSet `""` |
| VM power-on timeout | 5 minutes |

## How It Works

```
discover                         deploy
────────                         ──────
Artifactory API                  CSV file
    │                                │
    ▼                                ▼
ovf_cache.json ──────────────► OvfEntry list
                                     │
                              for each entry:
                                     │
                              ┌──────▼──────────────────────┐
                              │ 1. Download OVF descriptor   │
                              │    (detect vApp, parse props)│
                              │ 2. Upload to Content Library │
                              │    (PUSH local / PULL remote)│
                              │ 3. Wait for VMI in namespace │
                              │ 4. Create VirtualMachine CR  │
                              │ 5. Poll for PoweredOn state  │
                              │ 6. Record result + update    │
                              │    HTML report               │
                              └─────────────────────────────┘
```

Upload strategy:
- **Remote URLs** — vCenter performs a PULL transfer directly. The script
  fetches the full TLS certificate chain via `openssl s_client` and passes it
  to vCenter so it can verify the source. If the certificate is expired or
  untrusted, the OVF is marked `SKIPPED`.
- **Local files** — the script streams files to vCenter using chunked PUSH
  transfers, avoiding the 2 GB Python SSL memory limit for large VMDKs.
