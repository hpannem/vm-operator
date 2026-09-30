# Feature Specification: VirtualMachineDeployment

- **Feature branch**: `vm-deployment-sdd`
  - **Fork**: `vmware-tanzu/vm-operator`
  - **PR target**: `vmware-tanzu/vm-operator`
- **Created**: 2026-09-29
- **Status**: Draft
- **Epic**: vmop-1700
- **Design docs**: WIKI page 1007649487 — "VM Service: Workload Management of VMs using Kubernetes Patterns"

---

## Background

`VirtualMachineReplicaSet` (spec 009, epic vmop-1701) keeps N replicas that match one fixed template. It has no template revisions. A template change does not change any existing replica. `VirtualMachineDeployment` is the declarative layer above it. It follows the upstream Kubernetes `Deployment` rollout method.

When the user changes the VM template, the Deployment controller creates a new `VirtualMachineReplicaSet` for the new template. Each ReplicaSet has a hash of its template. The hash tells the ReplicaSets apart and prevents overlap. The Deployment then moves VMs from the old ReplicaSet to the new one. The update strategy controls how it does this. The old ReplicaSet stays with a replica count of 0, as history. The `revisionHistoryLimit` field sets how many old ReplicaSets the Deployment keeps (default 10).

To roll back, the user changes the template of the Deployment to match the template of an old ReplicaSet. The user can add labels or annotations, for example `kubernetes.io/change-cause`, to record why the rollout happened.

### Workload model: stateless replicas

Treat the VMs of a Deployment as stateless. A rollout replaces a VM. It does not update the VM in place. The controller deletes the old VM and creates a new VM from the new template. The new VM is a different VM. It has a new name and probably a new IP address. Data on the disks of the old VM is lost.

Shared data is still possible. The template can mount a network file share (NFS, or a vSAN File Services share) in the guest through its bootstrap data. The template can also refer to an existing PersistentVolumeClaim with `ReadWriteMany` access and `sharingMode: MultiWriter`. Per-replica persistent state belongs in `VirtualMachineStatefulSet`.

The `OnDelete` strategy is not part of this spec. Upstream has `OnDelete` for `StatefulSet` only.

This spec covers the full upstream Deployment feature set: `paused`, `progressDeadlineSeconds`, `minReadySeconds`, and the rollout commands of the `kubectl vsphere` plugin. The team delivers them one by one. See "Delivery order". The Deployment API has all fields from the first release. A later change to a shipped API is not safe.

## Goals

- MUST introduce a namespaced `VirtualMachineDeployment` CRD in `vmoperator.vmware.com/v1alpha6`. The spec has `replicas`, `selector`, `template`, `strategy`, and `revisionHistoryLimit`. The `template` field uses the `VirtualMachineTemplateSpec` type of `VirtualMachineReplicaSet`.
- MUST support two strategy types, as upstream does: `RollingUpdate` (default) and `Recreate`. `RollingUpdate` accepts `maxSurge` and `maxUnavailable`. It uses the upstream defaults (25%) and the upstream rounding rules.
- MUST create and own one `VirtualMachineReplicaSet` for each distinct template. The controller identifies the ReplicaSet with a hash of `spec.template`, including labels and annotations.
- MUST, with `RollingUpdate`, scale the new ReplicaSet up and the old ReplicaSets down in steps. Each reconcile does one step.
- MUST, with `RollingUpdate`, keep the total number of VMs at `replicas + maxSurge` or less. The number of available VMs must never be less than `replicas - maxUnavailable`.
- MUST, when both limits round to 0, use `maxUnavailable=1`, as upstream does.
- MUST, with `Recreate`, scale all old ReplicaSets to 0. The controller must not create the new ReplicaSet until no old VM is running. A VM that is being deleted still counts as running. Then the controller creates the new ReplicaSet at the full size.
- MUST scale old ReplicaSets down in the upstream order: not-ready VMs before ready VMs, and old revisions before the current revision.
- MUST, when the user changes `spec.replicas` during a rollout, share the change between the ReplicaSets in proportion to their sizes, as upstream does.
- MUST keep old ReplicaSets at 0 replicas as history. The controller deletes the oldest ones when their number is more than `revisionHistoryLimit`.
- MUST delete only a ReplicaSet that has `spec.replicas` 0 and `status.replicas` 0, has no pending change (its generation is not newer than its observed generation), and is not being deleted, as upstream does. Upstream does not count a ReplicaSet that is being deleted toward the history limit. The controller cleans up when the rollout is complete, and while the Deployment is paused.
- MUST support rollback. When the template of the Deployment matches an old ReplicaSet (the hash label ignored), the controller reuses that ReplicaSet. It does not create a new one.
- MUST give the reused ReplicaSet the next revision number. The old revision number goes into a `revision-history` annotation, so that `history` still shows it.
- MUST record the revision number on each ReplicaSet and on the Deployment.
- MUST copy the annotations of the Deployment to each ReplicaSet that it creates, for example `kubernetes.io/change-cause`. The controller skips a list of annotations: `last-applied-configuration` and the annotations that the controller owns. The `undo` command (not the controller) copies the annotations of the old ReplicaSet back to the Deployment, as `kubectl rollout undo` does upstream. A rollback by template edit does not copy them.
- MUST support `spec.paused`. While the value is true, the controller creates no ReplicaSet and starts no rollout for a template change. Scaling still works, and history cleanup still runs. When the value returns to false, the controller applies the latest template.
- MUST, when a deadline is set and `spec.paused` is true, set `Progressing` to Unknown with the reason `DeploymentPaused`. After the resume, the reason is `DeploymentResumed`. As a result, a long pause does not cause a timeout.
- MUST support `spec.progressDeadlineSeconds` (default 600). When a rollout makes no progress for that time, the controller sets `Progressing` to False with the reason `ProgressDeadlineExceeded`. The controller does not stop or undo the rollout.
- MUST define progress as one of these: more updated VMs, fewer old VMs, more ready VMs, or more available VMs. The value 2147483647 turns the deadline off, as upstream does.
- MUST support `spec.minReadySeconds` (default 0). A VM counts as available only after it was ready for that many seconds. The rolling update budgets use available VMs.
- MUST add `spec.minReadySeconds` and `status.availableReplicas` to `VirtualMachineReplicaSet`, with the same meaning.
- MUST provide the commands `kubectl vsphere vm rollout status|history|undo|pause|resume` for a `VirtualMachineDeployment`. Kubernetes `kubectl rollout` supports only built-in kinds. The existing `kubectl vsphere` plugin delivers these commands.
- MUST propagate `vmoperator.vmware.com/deployment-name` from the Deployment to its ReplicaSets and to their VMs. This resolves the `VirtualMachineDeploymentNameLabel` TODOs in the ReplicaSet controller.
- MUST adopt matching orphan ReplicaSets. MUST release owned ReplicaSets whose labels no longer match the selector. The semantics are the same as for `VirtualMachineReplicaSet`.
- MUST expose the `scale` subresource, so that `kubectl scale` works.
- MUST report these fields in status: `replicas`, `updatedReplicas`, `readyReplicas`, `availableReplicas`, `unavailableReplicas`, `collisionCount`, `lastProgressTime`, `observedGeneration`, `selector`, and conditions. The `selector` field is the string form of `spec.selector`. The upstream `scale` subresource returns it, and a CRD needs a string field for `labelSelectorPath`.
- MUST define `replicas` in status as the number of VMs that exist. `unavailableReplicas` is `replicas` minus `availableReplicas`.
- MUST be gated by the existing `K8sWorkloadMgmtAPI` feature flag. This spec adds no new flag.
- MUST ship with unit, integration, and E2E coverage.
- SHOULD reject an empty selector, a selector that does not match `spec.template.metadata.labels`, and a selector change after create.
- SHOULD show `Replicas`, `Updated`, `Ready`, and `Age` columns in `kubectl get`.

## Non-goals

- `OnDelete` strategy. Upstream supports it for `StatefulSet` only.
- Autoscaling (HPA/VPA) integration.
- Creating shared volumes for the user. The repo has no general ReadWriteMany provisioning. It only registers existing volumes. The user can use an existing shared volume or a network file share (see "Workload model").
- Per-replica volumes and stable VM identity. `VirtualMachineStatefulSet` delivers them.
- In-place update of a VM.
- `VirtualMachineStatefulSet`. It gets its own spec.
- Other changes to the `VirtualMachineReplicaSet` API. The ReplicaSet controller changes only for `minReadySeconds`, `availableReplicas`, label propagation, and VM removal order.
- Earlier API versions. The type exists in `v1alpha6` only.

## Delivery order

The team implements the work in this order. Each step ships with its own tests.

1. Core: types, `RollingUpdate`, `Recreate`, history, rollback, scale, proportional scaling, adoption. This step includes `status.availableReplicas` and `spec.minReadySeconds` on `VirtualMachineReplicaSet`, with their behavior. The rolling update budgets use available VMs, as upstream does, so the ReplicaSet must report them from the start.
2. `paused`.
3. `progressDeadlineSeconds`.
4. `minReadySeconds` on the Deployment.
5. `kubectl vsphere vm rollout` commands.

The Deployment API types include the fields of steps 2 to 4 from step 1. Until a step ships, the webhook rejects a non-default value of its field. As a result, no user relies on a field that does nothing.

## User stories / acceptance criteria

### DevOps user

- **Given** a new Deployment with `spec.replicas=3`, **When** the user creates it, **Then** one ReplicaSet owned by the Deployment exists, 3 VMs exist under it, and `status.replicas == status.updatedReplicas == 3`.
- **Given** a Deployment with 4 replicas and `RollingUpdate` (`maxSurge=1`, `maxUnavailable=1`), **When** the user changes `spec.template`, **Then** a new ReplicaSet is created. At every moment the Deployment has at most 5 VMs and at least 3 ready VMs. At the end, the new ReplicaSet has 4 VMs and the old ReplicaSet has 0.
- **Given** a Deployment that uses `Recreate`, **When** the user changes `spec.template`, **Then** all old VMs are removed before any new VM is created.
- **Given** a completed rollout, **When** the user lists the ReplicaSets of the Deployment, **Then** the old ReplicaSet is still present with 0 replicas and a lower revision number.
- **Given** more than `revisionHistoryLimit` old ReplicaSets exist, **When** the Deployment reconciles, **Then** the oldest ones are deleted.
- **Given** a Deployment that was rolled out, **When** the user changes `spec.template` back to the template of an old ReplicaSet, **Then** the controller reuses that ReplicaSet and scales it up. It scales the other ReplicaSets down.
- **Given** the user sets `kubernetes.io/change-cause` on the Deployment while the user edits the template, **When** the new ReplicaSet is created, **Then** the ReplicaSet has the same annotation.
- **Given** a rollout in progress, **When** the user runs `kubectl scale --replicas=10`, **Then** the added VMs are shared between the old and new ReplicaSets in proportion to their sizes.
- **Given** a Deployment, **When** the user deletes it, **Then** Kubernetes garbage-collects its ReplicaSets and their VMs.
- **Given** an existing ReplicaSet that matches the selector and has no controller owner, **When** the Deployment reconciles, **Then** the Deployment adopts it.

- **Given** a Deployment with `spec.paused=true`, **When** the user changes `spec.template`, **Then** no new ReplicaSet is created and no VM changes. Scaling with `kubectl scale` still works. **When** the user sets `spec.paused=false`, **Then** the rollout starts.
- **Given** a rollout where the new VMs never become ready, **When** `progressDeadlineSeconds` passes, **Then** `Progressing` is False with the reason `ProgressDeadlineExceeded`. **When** the VMs later become ready, **Then** the rollout completes and `Progressing` is True again.
- **Given** a Deployment with `minReadySeconds=30`, **When** a new VM becomes ready, **Then** the controller counts it as available only after 30 seconds. The rollout moves to the next step at that time.
- **Given** a Deployment, **When** the user runs `kubectl vsphere vm rollout status`, **Then** the command waits and reports progress until the rollout completes or fails. `history` lists the revisions with their change causes. `undo` returns to the previous revision or to a named revision. `pause` and `resume` set `spec.paused`.

- **Given** a template that mounts a network file share in the guest, **When** the Deployment rolls out, **Then** every replica, old and new, can use the share during the rollout.
- **Given** a rollout, **When** the old VM is deleted, **Then** the new VM is a new VM. Data on the disks of the old VM is not carried over.

### Tenant admin

- **Given** a Deployment with `maxSurge=0` and `maxUnavailable=0`, **When** the user creates it, **Then** admission rejects it.
- **Given** a Deployment with `progressDeadlineSeconds` that is not more than `minReadySeconds`, **When** the user creates it, **Then** admission rejects it.
- **Given** the `K8sWorkloadMgmtAPI` feature is disabled, **When** the controller-manager starts, **Then** it does not register the Deployment controller or the Deployment webhooks.

### Partner engineer

- **Given** the VMs of a Deployment, **When** the partner lists VMs with the label `vmoperator.vmware.com/deployment-name=<name>`, **Then** the VMs of all revisions are returned.
- **Given** a Deployment, **When** the partner reads `status.conditions`, **Then** `Available` and `Progressing` follow the standard condition pattern and show ReplicaSet failures.

## Open questions

- [NEEDS CLARIFICATION: The `kubectl vsphere` plugin code is not in this repository. Which repository and team implement the `vm rollout` commands? How do they take a dependency on the `v1alpha6` API? Owner: platform engineer and partner engineer.]


## Resolved decisions

- **Rollout method is the upstream Deployment method.** `OnDelete` is not supported for Deployments. This removes the earlier problem of an old ReplicaSet that recreates a deleted VM. The Deployment controller now controls the size of every ReplicaSet.
- **Scale-down order matches Kubernetes.** The Deployment reduces old revisions before the current revision, and not-ready VMs before ready VMs. Inside one ReplicaSet, the ReplicaSet controller picks the VMs to remove. It already removes VMs that are being deleted first. This spec adds more ranking: not-ready VMs before ready VMs, then VMs that became ready more recently before older ones, then newer VMs before older VMs. As upstream does, the ready-time and age comparisons use logarithmic buckets, so times that are close count as equal. The UID is the last tie-breaker.
- **Template hash covers the full `spec.template`, including labels and annotations,** as Kubernetes does. Any template change makes a new revision. A change to `spec.replicas` does not.
- **Surge and unavailable defaults match upstream (25% each).** Surge VMs use extra capacity during a rollout. A later change can tune the defaults after real use.
- **`VirtualMachineReplicaSet` gets `spec.minReadySeconds` and `status.availableReplicas`, as the upstream ReplicaSet has.** The Deployment copies its `minReadySeconds` to each ReplicaSet and reads `availableReplicas` from the status. The ReplicaSet controller computes availability from the `lastTransitionTime` of the VM `Ready` condition. Both fields are additive and optional. Their defaults keep today's behavior (`minReadySeconds=0`, so available equals ready). The `VirtualMachineReplicaSet` type is in the code, but no user has ever enabled the `K8sWorkloadMgmtAPI` feature flag. No user depends on the type, so the change is safe. The API and the behavior ship in the same change, so no field does nothing. This adds work to spec 009, which must record the new fields.
- **The `kubectl vsphere` plugin delivers the rollout commands.** The commands are `kubectl vsphere vm rollout status|history|undo|pause|resume`. The plugin already has VM commands, such as `kubectl vsphere vm web-console`. This repository provides the API that the commands use: revision annotations, `spec.paused`, status, and conditions.
- **Progress time is a status field.** Upstream uses `lastUpdateTime` for the deadline, but `metav1.Condition` has no such field. The status gets `lastProgressTime`. The controller sets it when it sees progress. It computes the timeout and the requeue time from it.
- **Stateless workload model.** A rollout replaces VMs. Shared data uses network file shares or existing `ReadWriteMany` MultiWriter volumes. The webhook does not reject volumes in the template. The docs state that a template PVC with `ReadWriteOnce` fails to attach on the second replica.
- **Name length matches upstream.** The ReplicaSet name is `<deployment-name>-<template-hash>`. If the name is too long for a DNS subdomain name, the controller cuts the Deployment part, as upstream does. The ReplicaSet controller names VMs with `generateName` (`<replicaset-name>-` plus 5 random characters). The API server cuts the base to fit 63 characters. A long Deployment name gives a cut VM name, but not a create error. This spec adds no extra name limit. A VM name can be too long for other reasons, for example for the network interface object name. Then it fails the same way as for a standalone VM.
- **A VM without a readiness probe is available at once.** The ReplicaSet controller already treats such a VM as ready (`isVMReady`), because it has no `Ready` condition. It has no `lastTransitionTime` either, so there is no signal to wait for. The controller counts it as available without waiting for `minReadySeconds`. A VM that has a `Ready` condition follows the upstream rule.
- **A VM that is being deleted still counts as a replica (existing `VirtualMachineReplicaSet` behavior, a deliberate difference from upstream).** Upstream counts only active pods. The ReplicaSet controller counts every owned VM, including VMs with a deletion timestamp, in `status.replicas` and in the difference from `spec.replicas`. This spec does not change that. Rationale: spec 009 requires `status.replicas` to equal the number of owned `VirtualMachine` objects (SC22), and it requires that no replacement is created while a deleting VM drains, so that the count does not overshoot `spec.replicas` (SC32). The code comment in `virtualmachinereplicaset_controller.go` states the same rule. A VM that is being deleted still uses vSphere capacity and storage until vSphere removes it, so counting it keeps the surge limit true in practice. No other rationale is recorded for the original choice. Effect on a rollout: the Deployment creates a replacement or surge VM only after the old VM is gone, so a slow delete (for example a finalizer) slows the rollout. The rolling update never exceeds `replicas + maxSurge` VM objects. `Recreate` also counts VMs that are being deleted, so both strategies behave the same here.
