# Model: VirtualMachineDeployment

API group and version: `vmoperator.vmware.com/v1alpha6`. This is a new type. The change is additive, and there is no conversion from older versions.

## Type

`VirtualMachineDeployment`, namespaced, short names `vmd` and `vmdeployment`. Markers: `+kubebuilder:object:root=true`, `+kubebuilder:subresource:status`, `+kubebuilder:subresource:scale:specpath=.spec.replicas,statuspath=.status.replicas,selectorpath=.status.selector`, `+kubebuilder:storageversion`.

### Spec

| Field | Type | Required | Default | Validation |
|-------|------|----------|---------|------------|
| `replicas` | `*int32` | optional | `1` | `Minimum=0`. A pointer, so that an explicit 0 is different from unset. |
| `selector` | `*metav1.LabelSelector` | required | — | Not empty (CEL). Must match `template.metadata.labels`. Immutable after create, with the CEL transition rule `self == oldSelf`. |
| `template` | `VirtualMachineTemplateSpec` | required | — | The type that `VirtualMachineReplicaSet` uses. |
| `strategy` | `VirtualMachineDeploymentStrategy` | optional | `{type: RollingUpdate}` | See below. |
| `revisionHistoryLimit` | `*int32` | optional | `10` | `Minimum=0`. |
| `paused` | `bool` | optional | `false` | While true, template changes do not start a rollout. |
| `progressDeadlineSeconds` | `*int32` | optional | `600` | `Minimum=1`. Must be more than `minReadySeconds`. The value 2147483647 turns the deadline off. |
| `minReadySeconds` | `int32` | optional | `0` | `Minimum=0`. Seconds a VM must be ready before it is available. |

`VirtualMachineDeploymentStrategy`:

| Field | Type | Default | Validation |
|-------|------|---------|------------|
| `type` | `string` | `RollingUpdate` | `+kubebuilder:validation:Enum=RollingUpdate;Recreate` |
| `rollingUpdate` | `*RollingUpdateVirtualMachineDeployment` | see below | Allowed only when `type` is `RollingUpdate`, with a CEL rule. |

`RollingUpdateVirtualMachineDeployment`:

| Field | Type | Default | Validation |
|-------|------|---------|------------|
| `maxSurge` | `*intstr.IntOrString` | `25%` | Integer or percentage, rounded up. |
| `maxUnavailable` | `*intstr.IntOrString` | `25%` | Integer or percentage, rounded down. `maxSurge` and `maxUnavailable` must not both be 0. At run time, both can round to 0. Then the controller uses `maxUnavailable=1`. |

### Status

| Field | Type | Meaning |
|-------|------|---------|
| `replicas` | `int32` | Number of VMs that exist in all owned ReplicaSets (the sum of their `status.replicas`). |
| `updatedReplicas` | `int32` | Number of VMs that exist in the ReplicaSet for the current template. |
| `readyReplicas` | `int32` | Number of VMs with the `Ready` condition `True`, in all revisions. |
| `availableReplicas` | `int32` | Number of ready VMs that have been ready for `minReadySeconds` or more. |
| `unavailableReplicas` | `int32` | `replicas` minus `availableReplicas`, and not less than 0. |
| `collisionCount` | `*int32` | Number of hash collisions. The controller uses it to make a unique ReplicaSet name. |
| `lastProgressTime` | `*metav1.Time` | The last time that the controller saw progress in a rollout. The progress deadline and its requeue time use it. |
| `selector` | `string` | The string form of `spec.selector` (`metav1.LabelSelectorAsSelector(...).String()`). The `scale` subresource uses it as `selectorpath`. The controller sets it. |
| `observedGeneration` | `int64` | The last generation that the controller reconciled. |
| `conditions` | `[]metav1.Condition` | See below. |

### Conditions

| Type | Status and reason |
|------|-------------------|
| `Available` | True: `MinimumReplicasAvailable` when `availableReplicas` is `spec.replicas - maxUnavailable` or more. False: `MinimumReplicasUnavailable`. |
| `Progressing` | True: `NewReplicaSetCreated`, `FoundNewReplicaSet`, `ReplicaSetUpdated` (progress seen), `NewReplicaSetAvailable` (rollout complete). False: `ProgressDeadlineExceeded`, `ReplicaSetCreateError`. Unknown: `DeploymentPaused`, `DeploymentResumed`. The controller removes this condition when the deadline is off. |
| `ReplicaFailure` | Copied from the `ReplicaFailure` condition of the new ReplicaSet, or else of an old ReplicaSet. Removed when none exists. |
| `Ready` | True when `Available` is true, the rollout is complete, and the last reconcile had no error. False: `RolloutInProgress`, `ReconcileError`. This repository requires this condition. It has no upstream equivalent. |

A rollout is complete when `updatedReplicas`, `replicas`, and `availableReplicas` all equal `spec.replicas` and `observedGeneration` is not less than the generation.

The reason constants live in the API package next to the type. The controller must not use raw strings.

## Change to `VirtualMachineReplicaSet` (`v1alpha6`)

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `spec.minReadySeconds` | `int32` | optional | `0` | `Minimum=0`. Seconds a VM must be ready before it is available. |
| `status.availableReplicas` | `int32` | optional | `0` | Number of ready VMs that have been ready for `spec.minReadySeconds` or more. |

Both fields are additive and optional (`+optional`, `omitempty`). With the default, available equals ready. The Deployment sets `spec.minReadySeconds` on each ReplicaSet it owns, and sums `status.availableReplicas` for its own status. The `kubectl get` output of the ReplicaSet gets an `Available` column.

## Labels, annotations, and names

| Item | Value |
|------|-------|
| Deployment name label | `vmoperator.vmware.com/deployment-name` on ReplicaSets and VMs. The value comes from `pkgutil.MustFormatValue`. |
| Template hash label | `vmoperator.vmware.com/template-hash` on the ReplicaSet metadata, selector, and template. |
| Revision annotation | `vmoperator.vmware.com/revision` on the Deployment and on each ReplicaSet. |
| Revision history | `vmoperator.vmware.com/revision-history` on a ReplicaSet: the list of earlier revision numbers that it served. |
| Proportional scaling | `vmoperator.vmware.com/desired-replicas` and `vmoperator.vmware.com/max-replicas` on each ReplicaSet, as upstream stores them. |
| Annotation copy | The controller copies all Deployment annotations to a new ReplicaSet, for example `kubernetes.io/change-cause`. It skips `kubectl.kubernetes.io/last-applied-configuration` and the revision, revision-history, desired-replicas, and max-replicas annotations. |
| ReplicaSet name | `<deployment-name>-<template-hash>`. The controller cuts the Deployment part if the result is too long for a DNS subdomain name, as upstream does. VM names are `<replicaset-name>-<5 random characters>`, cut by the API server to 63 characters. |
| Finalizer | None. Owner references garbage-collect the ReplicaSets and VMs, as upstream does. |

## Example

```yaml
apiVersion: vmoperator.vmware.com/v1alpha6
kind: VirtualMachineDeployment
metadata:
  name: web
  annotations:
    kubernetes.io/change-cause: "Move to image vmi-0123"
spec:
  replicas: 4
  revisionHistoryLimit: 10
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 1
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      className: small
      imageName: vmi-0123456789
      storageClass: wcpglobal-storage-profile
```
