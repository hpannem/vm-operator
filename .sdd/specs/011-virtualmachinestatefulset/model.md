# Data Model: VirtualMachineStatefulSet

- **Spec**: [`spec.md`](./spec.md)
- **API version**: `vmoperator.vmware.com/v1alpha6`
- **Kind**: `VirtualMachineStatefulSet` (short name `vmss`), list kind `VirtualMachineStatefulSetList`
- **Scope**: Namespaced
- **Go file**: `api/v1alpha6/virtualmachinestatefulset_types.go`

Field names and defaults follow `apps/v1` `StatefulSet`. A VM takes the place of a Pod.

## Spec

| Field | Type | Marker | Default | Validation | Immutable |
|-------|------|--------|---------|------------|-----------|
| `replicas` | `*int32` | optional | `1` | `>= 0` | No |
| `selector` | `*metav1.LabelSelector` | required | none | Must match `template.metadata.labels`. Must not be empty. | Yes |
| `template` | `VirtualMachineTemplateSpec` | required | none | Same type as `VirtualMachineReplicaSet`. | No |
| `serviceName` | `string` | required | none | DNS label. Names a headless `VirtualMachineService`. | Yes |
| `volumeClaimTemplates` | `[]PersistentVolumeClaimTemplate` | optional | none | Names are unique. Names must not collide with `template.spec.volumes` names. | Yes |
| `podManagementPolicy` | `string` (enum) | optional | `OrderedReady` | `OrderedReady` or `Parallel` | No |
| `updateStrategy` | `VirtualMachineStatefulSetUpdateStrategy` | optional | type `RollingUpdate` | See below. | No |
| `persistentVolumeClaimRetentionPolicy` | `*VirtualMachineStatefulSetPersistentVolumeClaimRetentionPolicy` | optional | both `Retain` | See below. | No |
| `revisionHistoryLimit` | `*int32` | optional | `10` | `>= 0` | No |
| `minReadySeconds` | `int32` | optional | `0` | `>= 0`. A VM is available when `Ready` was true for this long. | No |

The field `podManagementPolicy` is named for parity with upstream. Because the type is a VM and not a Pod, the plan may rename it. The spec decision is to keep the upstream name so tools and habits carry over.

### `updateStrategy`

| Field | Type | Marker | Default | Validation |
|-------|------|--------|---------|------------|
| `type` | `string` (enum) | optional | `RollingUpdate` | `RollingUpdate` or `OnDelete` |
| `rollingUpdate` | `*RollingUpdateVirtualMachineStatefulSetStrategy` | optional | none | Allowed only when `type` is `RollingUpdate`. |
| `rollingUpdate.partition` | `*int32` | optional | `0` | `>= 0`. VMs with an ordinal below `partition` are not updated. |
| `rollingUpdate.maxUnavailable` | `*intstr.IntOrString` | optional | `1` | Integer `>= 1`, or percent. Rounds up. |

### `persistentVolumeClaimRetentionPolicy`

| Field | Type | Marker | Default | Validation |
|-------|------|--------|---------|------------|
| `whenDeleted` | `string` (enum) | optional | `Retain` | `Retain` or `Delete`. Applies when the set is deleted. |
| `whenScaled` | `string` (enum) | optional | `Retain` | `Retain` or `Delete`. Applies when replicas decrease. |

### `volumeClaimTemplates`

Each item has `metadata` (name, labels, annotations) and a `spec` of type `corev1.PersistentVolumeClaimSpec`. The PVC name is `<template-name>-<statefulset-name>-<ordinal>`, as upstream. The controller adds a volume to the VM for each PVC.

## Status

| Field | Type | Meaning |
|-------|------|---------|
| `observedGeneration` | `int64` | Last generation the controller processed. |
| `replicas` | `int32` | Number of VMs that the controller created. |
| `readyReplicas` | `int32` | Number of VMs that are Ready. |
| `currentReplicas` | `int32` | Number of VMs that use `currentRevision`. |
| `updatedReplicas` | `int32` | Number of VMs that use `updateRevision`. |
| `availableReplicas` | `int32` | Number of VMs whose `Ready` condition was true for at least `minReadySeconds`, measured from its `lastTransitionTime`. |
| `currentRevision` | `string` | Name of the `ControllerRevision` that the VMs with ordinal below `partition` use. |
| `updateRevision` | `string` | Name of the `ControllerRevision` that the new VMs use. |
| `collisionCount` | `*int32` | Number of hash collisions when the controller names a revision. |
| `conditions` | `[]metav1.Condition` | See below. |

A VM is Ready by the same rule as `VirtualMachineReplicaSet`: the `Ready` condition is true. VMs have a readiness probe, so the condition is the signal for `OrderedReady`, `maxUnavailable`, and `minReadySeconds`.

### Conditions

| Type | Status | Reason | Meaning |
|------|--------|--------|---------|
| `Ready` | `True` | `AllReplicasReady` | `readyReplicas` equals `replicas`. |
| `Ready` | `False` | `ScalingUp`, `ScalingDown`, `Updating`, `VirtualMachinesNotReady` | The set has not reached the desired state. |
| `ReplicaFailure` | `True` | `VirtualMachineCreationFailed`, `VirtualMachineDeletionFailed`, `PersistentVolumeClaimFailed` | The controller could not create or delete a VM or PVC. |

The reason constants reuse the `VirtualMachineReplicaSet` constants where the names match. New constants go in the same API package.

## Labels and annotations on VMs

| Key | Value | Purpose |
|-----|-------|---------|
| `vmoperator.vmware.com/statefulset-name` | set name | Owner lookup and selector. |
| `vmoperator.vmware.com/statefulset-vm-name` | VM name | Select one VM. |
| `vmoperator.vmware.com/statefulset-vm-index` | ordinal | Select one ordinal. |
| `controller-revision-hash` | `ControllerRevision` name | Revision of the VM, as upstream. |

[NEEDS CLARIFICATION: Final label key names. The keys above are proposals. Owner: platform engineer.]

## ControllerRevision

The controller uses `apps/v1` `ControllerRevision`. It stores the template as data. It sets the set as the controller owner. It hashes the data to make the name, and uses `collisionCount` to resolve a hash collision.

## Validation split

- **CEL:** enum values, `>= 0` checks, and the `maxUnavailable` and `partition` type rules.
- **Webhook:** `selector` matches `template.metadata.labels`, the immutable fields `selector`, `serviceName`, and `volumeClaimTemplates`, and the unique volume name rules.
- **Defaults:** the mutation webhook or CRD defaults set the values in the table above.

## Subresources

- `status`
- `scale`, with `specReplicasPath` `.spec.replicas`, `statusReplicasPath` `.status.replicas`, and `labelSelectorPath` from the selector.

## Conversion

The kind exists only in `v1alpha6`. No conversion to earlier versions is necessary. The plan must confirm how the repository handles kinds that are new in the newest version.

## Example

```yaml
apiVersion: vmoperator.vmware.com/v1alpha6
kind: VirtualMachineStatefulSet
metadata:
  name: db
spec:
  replicas: 3
  serviceName: db
  podManagementPolicy: OrderedReady
  updateStrategy:
    type: RollingUpdate
    rollingUpdate:
      partition: 0
      maxUnavailable: 1
  persistentVolumeClaimRetentionPolicy:
    whenDeleted: Retain
    whenScaled: Retain
  revisionHistoryLimit: 10
  selector:
    matchLabels:
      app: db
  template:
    metadata:
      labels:
        app: db
    spec:
      className: best-effort-small
      imageName: vmi-0123456789
      storageClass: standard
  volumeClaimTemplates:
  - metadata:
      name: data
    spec:
      accessModes: [ReadWriteOnce]
      storageClassName: standard
      resources:
        requests:
          storage: 20Gi
---
apiVersion: vmoperator.vmware.com/v1alpha6
kind: VirtualMachineService
metadata:
  name: db
spec:
  type: ClusterIP
  clusterIP: None
  selector:
    app: db
```
