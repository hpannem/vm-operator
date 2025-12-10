# IPv6 and Dual-Stack Integration Test Automation

## Overview

This automation script runs IPv6-only and dual-stack network integration tests for VirtualMachines using the VDS (NetOP) network provider. It automates the creation of VMs, network configuration verification, and provides detailed test results.

The script executes 27 test cases covering IPv6-only and dual-stack scenarios across different network modes (StaticPool, DHCP, NoIPAM) and bootstrap providers (Cloud-Init, GOSC), including multi-interface configurations.

For detailed test case specifications, see [ipv6-dual-stack-integration-tests-vds.md](../../docs/test-cases/ipv6-dual-stack-integration-tests-vds.md).

## Prerequisites

### Required Software
- `kubectl` - Kubernetes command-line tool (v1.20+)
- `jq` - JSON processor for parsing kubectl output
- `bash` - Version 4.0 or higher
- `openssl` - For random number generation (optional, falls back to $RANDOM)

### Cluster Requirements
- Kubernetes cluster with VM Operator installed
- NetOP (Network Operator) installed and configured
- Access to vSphere environment with Distributed Virtual Switches

### Cluster Resources
- **VM Class**: Must exist in cluster (default: `best-effort-xsmall`)
  - Verify: `kubectl get vmclass best-effort-xsmall`
- **StorageClass**: Must exist in cluster (default: `wcpglobal-storage-profile`)
  - Verify: `kubectl get storageclass wcpglobal-storage-profile`
- **VirtualMachineImage** or **ClusterVirtualMachineImage**: At least one must be available
  - Verify: `kubectl get vmi -n <namespace>` or `kubectl get cvmi`
- **Network Resources**: vSphere Distributed Port Groups configured for:
  - StaticPool mode (for StaticPool test cases)
  - DHCP mode (for DHCP test cases, optional if same as StaticPool)
  - NoIPAM mode (for NoIPAM test cases, optional if same as StaticPool)

### Permissions
- Create VirtualMachine resources in target namespace
- Create NetworkInterface resources (via VM Operator)
- Create Secrets (for Cloud-Init and LinuxPrep bootstrap)
- Read VM status and NetworkInterface status
- Delete VMs (if using `--cleanup` flag)

## Installation

No installation required. The script is self-contained.

```bash
# Make script executable
chmod +x hack/test-ipv6/test-ipv6.sh
chmod +x hack/test-ipv6/*.sh
```

## Usage

### Basic Command Structure

```bash
./hack/test-ipv6/test-ipv6.sh \
  --namespace <namespace> \
  --kubeconfig <path-to-kubeconfig> \
  --primary-network <network-name> \
  [OPTIONS]
```

### Required Parameters

- `--namespace`: Kubernetes namespace where VMs will be created
- `--kubeconfig`: Path to kubeconfig file for cluster access
- `--primary-network`: Primary network name (vSphere Distributed Port Group)

### Optional Parameters

- `--staticpool-network`: Network for StaticPool mode tests (default: primary-network)
- `--dhcp-network`: Network for DHCP mode tests (default: primary-network)
- `--noipam-network`: Network for NoIPAM mode tests (default: primary-network)
- `--cleanup`: Cleanup VMs after testing (default: false)
- `--timeout`: Timeout for VM Ready condition (default: 5m)
- `--list`: List all available test cases and exit
- `--test-case`: Run specific test case(s) by ID (e.g., TC-001 or TC-001,TC-002)

## Command Examples

### List All Test Cases

```bash
./hack/test-ipv6/test-ipv6.sh --list
```

This will display all 27 test cases with their IDs, names, bootstrap providers, IP families, and network categories.

### Run All Test Cases

```bash
./hack/test-ipv6/test-ipv6.sh \
  --namespace telco-ns \
  --kubeconfig ~/.kube/config \
  --primary-network primary-network
```

### Run Specific Test Case

```bash
./hack/test-ipv6/test-ipv6.sh \
  --namespace telco-ns \
  --kubeconfig ~/.kube/config \
  --primary-network primary-network \
  --test-case TC-001
```

### Run Multiple Test Cases

```bash
./hack/test-ipv6/test-ipv6.sh \
  --namespace telco-ns \
  --kubeconfig ~/.kube/config \
  --primary-network primary-network \
  --test-case TC-001,TC-002,TC-010
```

### Run with Different Networks per Category

If you have separate networks configured for different IP assignment modes:

```bash
./hack/test-ipv6/test-ipv6.sh \
  --namespace telco-ns \
  --kubeconfig ~/.kube/config \
  --primary-network primary-network \
  --staticpool-network staticpool-net \
  --dhcp-network dhcp-net \
  --noipam-network noipam-net
```

### Run with Cleanup Enabled

Automatically delete VMs after each test completes:

```bash
./hack/test-ipv6/test-ipv6.sh \
  --namespace telco-ns \
  --kubeconfig ~/.kube/config \
  --primary-network primary-network \
  --cleanup
```

### Run with Custom Timeout

Increase timeout for slower environments:

```bash
./hack/test-ipv6/test-ipv6.sh \
  --namespace telco-ns \
  --kubeconfig ~/.kube/config \
  --primary-network primary-network \
  --timeout 10m
```

### Combined Example

```bash
./hack/test-ipv6/test-ipv6.sh \
  --namespace telco-ns \
  --kubeconfig ~/.kube/config \
  --primary-network primary-network \
  --staticpool-network staticpool-net \
  --dhcp-network dhcp-net \
  --test-case TC-001,TC-010,TC-016 \
  --cleanup \
  --timeout 10m
```

## Test Case Categories

The script includes 27 test cases organized into 7 categories:

1. **Category 1: NetOP StaticPool Mode** (TC-001 to TC-005)
   - IPv6-only and dual-stack scenarios with NetOP StaticPool IP assignment
   - Tests automatic IP assignment from NetOP StaticPool

2. **Category 3: NetOP None Mode** (TC-006)
   - NoIPAM scenarios where no IP assignment is performed

3. **Category 4: User-Specified Overrides** (TC-007 to TC-013)
   - User addresses, gateways, and DHCP flags overriding NetOP configuration
   - Tests user override behavior and gateway backfill

4. **Category 5: Bootstrap Provider Variations** (TC-014 to TC-019)
   - GOSC (LinuxPrep) and Cloud-Init bootstrap provider variations
   - Tests different bootstrap providers with various network configurations

5. **Category 6: Edge Cases** (TC-020 to TC-023)
   - Edge cases like no gateways, multiple addresses, partial gateway backfill, different subnet masks

6. **Category 7: Primary IPv4 + Secondary Network** (TC-024 to TC-027)
   - Multi-interface test cases with primary IPv4 network and secondary network (IPv6, dual-stack, NoIPAM, or DHCP)
   - Tests scenarios where primary interface is IPv4 and secondary interface has different IP family configurations

## Understanding Results

### Results Table

The script outputs a table with the following columns:
- **Test ID**: Test case identifier (e.g., TC-001)
- **Test Case Name**: Descriptive name of the test
- **Bootstrap**: Bootstrap provider used (Cloud-Init or GOSC)
- **IP Family**: Expected IP family (IPv6-only, Dual-stack, Multi-interface, N/A)
- **Status**: PASS or FAIL
- **Reason**: Brief reason for pass/fail

Example output:
```
Test ID     Test Case Name                              Bootstrap      IP Family      Status   Reason
--------    ----------------------------------------    ----------     ----------     ------   ------
TC-001      IPv6-Only StaticPool from NetOP            Cloud-Init     IPv6-only      PASS     Network configuration verified
TC-002      Dual-Stack StaticPool from NetOP            Cloud-Init     Dual-stack     FAIL     IP family mismatch
TC-024      Primary IPv4 + Secondary IPv6              Cloud-Init     Multi-interface PASS     Network configuration verified
```

### Failure Diagnostics

For failed test cases, detailed diagnostics are provided in a separate section after the results table. This includes:
- VM Ready status and conditions
- Actual IP addresses assigned
- Expected vs actual IP families
- NetworkInterface CR status (IPAssignmentMode, IPConfigs)
- DHCP status (if applicable)
- Specific error messages

Example diagnostics:
```
==========================================
Detailed Failure Diagnostics
==========================================

--- TC-002: Dual-Stack StaticPool from NetOP ---
VM Ready Status: True
IP Addresses: 192.168.1.100
Expected IP Family: Dual-stack
Actual IP Family: IPv4-only (IPv4=true, IPv6=false)
NetworkInterface IPAssignmentMode: StaticPool
NetworkInterface IPConfigs: [{"ip":"192.168.1.100","ipFamily":"IPv4","gateway":"192.168.1.1"}]
Error Details: Expected dual-stack but only IPv4 address assigned. IPv6 address missing.
```

## Troubleshooting

### Common Issues

#### VM fails to become Ready

**Symptoms**: Test fails with "VM failed to become Ready"

**Debugging**:
```bash
# Check VM events
kubectl describe vm <vm-name> -n <namespace>

# Check VM conditions
kubectl get vm <vm-name> -n <namespace> -o yaml

# Verify VM Class exists
kubectl get vmclass best-effort-xsmall

# Verify StorageClass exists
kubectl get storageclass wcpglobal-storage-profile
```

**Common Causes**:
- VM Class or StorageClass not found
- Insufficient resources in cluster
- Image pull issues
- Network configuration problems

#### No IP addresses assigned

**Symptoms**: Test fails with "No IP addresses assigned"

**Debugging**:
```bash
# Check NetworkInterface CR
kubectl get networkinterface -n <namespace> -l vmoperator.vmware.com/vm-name=<vm-name>

# Check NetworkInterface status
kubectl get networkinterface <netif-name> -n <namespace> -o yaml

# Verify network is configured correctly in NetOP
kubectl get network <network-name> -n <namespace>
```

**Common Causes**:
- Network not configured in NetOP
- NetworkInterface CR not created
- IPAssignmentMode not set correctly
- Network pool exhausted

#### IP family mismatch

**Symptoms**: Test fails with "IP family mismatch"

**Debugging**:
```bash
# Check assigned IP addresses
kubectl get vm <vm-name> -n <namespace> -o jsonpath='{.status.network.interfaces[*].ipAddresses[*]}'

# Check NetworkInterface IPConfigs
kubectl get networkinterface <netif-name> -n <namespace> -o jsonpath='{.status.IPConfigs}'

# Verify network supports expected IP family
kubectl get network <network-name> -n <namespace> -o yaml
```

**Common Causes**:
- Network only supports IPv4, but test expects IPv6 or dual-stack
- NetOP network configuration doesn't include IPv6
- Network pool doesn't have IPv6 addresses available

#### DHCP not working

**Symptoms**: Test fails with "DHCP4 expected but not enabled" or "DHCP6 expected but not enabled"

**Debugging**:
```bash
# Check DHCP status in VM
kubectl get vm <vm-name> -n <namespace> -o jsonpath='{.status.network.config.interfaces[?(@.name=="eth0")].ip.dhcp}'

# Check DHCP flags in VM spec
kubectl get vm <vm-name> -n <namespace> -o jsonpath='{.spec.network.interfaces[?(@.name=="eth0")].dhcp4}'
kubectl get vm <vm-name> -n <namespace> -o jsonpath='{.spec.network.interfaces[?(@.name=="eth0")].dhcp6}'

# Check NetworkInterface IPAssignmentMode
kubectl get networkinterface <netif-name> -n <namespace> -o jsonpath='{.status.IPAssignmentMode}'
```

**Common Causes**:
- Network not configured for DHCP mode
- DHCP server not available on network
- NetworkInterface IPAssignmentMode not set to DHCP
- User DHCP flags not properly applied

### Manual Verification Commands

```bash
# Check VM status
kubectl get vm <vm-name> -n <namespace> -o yaml

# Check NetworkInterface CR
kubectl get networkinterface -n <namespace> -l vmoperator.vmware.com/vm-name=<vm-name>

# Check VM IP addresses
kubectl get vm <vm-name> -n <namespace> -o jsonpath='{.status.network.interfaces[*].ipAddresses[*]}'

# Check NetworkInterface IPAssignmentMode
kubectl get networkinterface <netif-name> -n <namespace> -o jsonpath='{.status.IPAssignmentMode}'

# Check NetworkInterface IPConfigs
kubectl get networkinterface <netif-name> -n <namespace> -o jsonpath='{.status.IPConfigs}' | jq

# Check VM network config
kubectl get vm <vm-name> -n <namespace> -o jsonpath='{.status.network.config}' | jq
```

## Cleanup

### Automatic Cleanup

Use the `--cleanup` flag to automatically delete VMs after each test completes:

```bash
./hack/test-ipv6/test-ipv6.sh ... --cleanup
```

This will:
- Delete each VM immediately after its test completes
- Clean up existing test VMs at the start (if cleanup flag is set)
- Optionally clean up secrets (currently commented out)

### Manual Cleanup

If you need to manually clean up test resources:

```bash
# Delete all test VMs (by name pattern)
kubectl get vm -n <namespace> -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | grep '^tc[0-9]\+-' | xargs -I {} kubectl delete vm {} -n <namespace>

# Or delete specific VM
kubectl delete vm tc001-ipv6-only -n <namespace>

# Delete secrets (optional)
kubectl delete secret vm-cloud-init-bootstrap-data linuxprep-password -n <namespace> --ignore-not-found=true

# Delete NetworkInterface CRs (usually garbage collected automatically)
kubectl delete networkinterface -n <namespace> -l vmoperator.vmware.com/vm-name=<vm-name>
```

## Script Structure

The test automation suite consists of the following files:

- **`test-ipv6.sh`**: Main orchestration script
  - Parses command-line arguments
  - Discovers prerequisites (VMI, VM Class, StorageClass)
  - Executes test cases
  - Collects and displays results

- **`lib.sh`**: Helper function library
  - VMI discovery
  - Secret creation
  - VM network verification
  - Diagnostic collection
  - Results table formatting

- **`cases-staticpool.sh`**: StaticPool mode test cases (TC-001 to TC-005)
- **`cases-none.sh`**: NoIPAM mode test cases (TC-006)
- **`cases-user-overrides.sh`**: User override test cases (TC-007 to TC-013)
- **`cases-bootstrap-variations.sh`**: Bootstrap provider variation test cases (TC-014 to TC-019)
- **`cases-edge.sh`**: Edge case test cases (TC-020 to TC-023)
- **`cases-primary-ipv4-secondary.sh`**: Primary IPv4 + Secondary Network test cases (TC-024 to TC-027)

## Adding New Test Cases

To add a new test case:

1. **Add test case function** to the appropriate `cases-*.sh` file:
   ```bash
   tc024_new_test_case() {
       local namespace="$1"
       local primary_network="$2"
       local vmi_id="$3"
       local vm_class="$4"
       local storage_class="$5"

       local vm_name="tc024-new-test-case"

       cat <<EOF
   apiVersion: vmoperator.vmware.com/v1alpha5
   kind: VirtualMachine
   metadata:
     name: $vm_name
     namespace: $namespace
   spec:
     # ... VM spec ...
   EOF
   }
   ```

2. **Add test case entry** to the test case registry in `test-ipv6.sh`:
   ```bash
   "TC-024|New Test Case Name|Cloud-Init|Dual-stack|none|tc024_new_test_case|StaticPool|Category X: ..."
   ```

3. **Add test case call** in the main function (or use dynamic execution based on registry)

4. **Update test case documentation** in `docs/test-cases/ipv6-dual-stack-integration-tests-vds.md`

## Support

For issues or questions, refer to:
- Test case documentation: `docs/test-cases/ipv6-dual-stack-integration-tests-vds.md`
- VM Operator documentation
- NetOP (Network Operator) documentation

## Exit Codes

- `0`: All tests passed
- `1`: One or more tests failed or error occurred

The script will exit with code 1 if any test case fails, allowing it to be used in CI/CD pipelines.

