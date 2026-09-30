# Feature Specification: VirtualMachineStatefulSet

- **Feature branch**: `feature/virtualmachinestatefulset`
  - **Fork**: `vmware-tanzu/vm-operator`
  - **PR target**: `vmware-tanzu/vm-operator`
- **Created**: 2026-09-30
- **Status**: Draft
- **Epic**: vmop-1837
- **Design docs**: WIKI page 100764

---

## Background

`VirtualMachineReplicaSet` (spec 009) keeps N identical VMs running. Its VMs have random names, share no storage identity, and start and stop in any order. This is correct for stateless workloads. It is not correct for clustered workloads, such as databases and consensus systems, that need a stable name, stable storage, and controlled start and update order for each member.

`VirtualMachineStatefulSet` gives a group of VMs these properties. It follows the Kubernetes `StatefulSet` API and behavior. Where this spec does not state a difference, the upstream Kubernetes behavior applies. The purpose is that a user who knows `StatefulSet` can use `VirtualMachineStatefulSet` without a new mental model.

## Goals

- MUST add a namespaced `VirtualMachineStatefulSet` kind, short name `vmss`, to API version `v1alpha6` in group `vmoperator.vmware.com`.
- MUST give each replica a stable identity:
  - The VM name is `<statefulset-name>-<ordinal>`, with ordinals `0` to `replicas-1`.
  - Each VM carries a label with its ordinal and a label with its name.
  - A replaced VM gets the same name.
- MUST support `volumeClaimTemplates`. The controller creates one PVC per replica per template. A replaced VM reattaches to the PVC of the same ordinal.
- MUST support `persistentVolumeClaimRetentionPolicy` with `whenDeleted` and `whenScaled`. Each value is `Retain` or `Delete`. The default for both is `Retain`, as upstream.
- MUST support `serviceName`. The name refers to a headless `VirtualMachineService` that the user creates. The controller does not create it.
- MUST support `podManagementPolicy` with the values `OrderedReady` and `Parallel`. The default is `OrderedReady`, as upstream.
  - `OrderedReady`: the controller creates replicas in ascending order and removes them in descending order. It waits for the previous replica to be Ready (create) or gone (remove) before it acts on the next.
  - `Parallel`: the controller creates and removes replicas at the same time, with no wait.
- MUST support `updateStrategy` with the types `RollingUpdate` and `OnDelete`. The default type is `RollingUpdate`, as upstream.
  - `RollingUpdate` MUST support `partition` and `maxUnavailable`.
  - `OnDelete` MUST NOT update a VM until the user deletes it.
- MUST record each template revision as a `ControllerRevision`. The status MUST show `currentRevision`, `updateRevision`, and `collisionCount`.
- MUST support `revisionHistoryLimit`. The default is `10`, as upstream. The controller keeps the newest revisions that are within the limit and the revisions that a replica uses.
- MUST support rollback with `kubectl rollout undo` and history with `kubectl rollout history`, as upstream.
- MUST propagate a change to template metadata (labels and annotations) to existing VMs in place, without a rollout.
- MUST report the upstream status counts: `replicas`, `readyReplicas`, `currentReplicas`, `updatedReplicas`, and `availableReplicas`. It MUST report `observedGeneration` and a `Ready` condition.
- MUST support the `scale` subresource.
- MUST support `minReadySeconds`. A VM is available when its `Ready` condition has been true for at least `minReadySeconds`. The default is `0`, so available equals ready.
- MUST be gated by the `supports_k8s_workload_mgmt_api` capability (the `K8sWorkloadMgmtAPI` feature flag), the same gate as `VirtualMachineReplicaSet`. With the gate off, the controller and webhooks are not registered. This spec adds no new gate.
- MUST NOT allow a user to change `selector`, `serviceName`, or `volumeClaimTemplates` after create. This matches upstream.
- SHOULD ship with unit, integration, and E2E tests in the same change set as the behavior.

## Non-goals

- Custom start ordinals. Ordinals always start at `0`.
- Autoscaling with `HorizontalPodAutoscaler`.
- Shared ReadWriteMany volumes across replicas.
- Use of `spec.state` on the template VM.
- A `VirtualMachineDeployment` kind. It is planned as a separate spec and builds on `VirtualMachineReplicaSet`.
- Creation of the headless `VirtualMachineService` by the controller.

## User stories / acceptance criteria

### DevOps user: stable identity

- **Given** a `VirtualMachineStatefulSet` named `db` with `replicas: 3`, **When** the controller reconciles it, **Then** `kubectl get vm` shows `db-0`, `db-1`, and `db-2`, each with the ordinal label and the name label.
- **Given** VM `db-1` is deleted, **When** the controller reconciles, **Then** a new VM named `db-1` exists and it uses the same PVCs as before.

### DevOps user: storage

- **Given** `volumeClaimTemplates` with one template named `data`, **When** the set scales to 3, **Then** three PVCs exist, one for each ordinal, and each VM has the volume for its own PVC.
- **Given** `whenScaled: Retain`, **When** the set scales from 3 to 1, **Then** the PVCs of `db-1` and `db-2` remain.
- **Given** `whenScaled: Delete`, **When** the set scales from 3 to 1, **Then** the PVCs of `db-1` and `db-2` are deleted after the VMs are gone.
- **Given** `whenDeleted: Delete`, **When** the set is deleted, **Then** all PVCs of the set are deleted. With `Retain`, all PVCs remain.

### DevOps user: ordering

- **Given** `podManagementPolicy: OrderedReady`, **When** the set scales from 0 to 3, **Then** `db-1` does not exist until `db-0` is Ready, and `db-2` does not exist until `db-1` is Ready.
- **Given** `OrderedReady`, **When** the set scales from 3 to 1, **Then** `db-2` is removed and gone before `db-1` is removed.
- **Given** `podManagementPolicy: Parallel`, **When** the set scales from 0 to 3, **Then** all three VMs exist without a wait between them.

### DevOps user: updates

- **Given** `RollingUpdate` and a changed template spec, **When** the controller reconciles, **Then** it updates VMs from the highest ordinal to the lowest, and no more than `maxUnavailable` VMs are unavailable at one time.
- **Given** `RollingUpdate` with `partition: 2` and 3 replicas, **When** the template changes, **Then** only `db-2` is updated.
- **Given** `OnDelete` and a changed template spec, **When** the controller reconciles, **Then** no VM changes. **When** the user deletes `db-1`, **Then** the new `db-1` uses the new template.
- **Given** a change to only the template labels or annotations, **When** the controller reconciles, **Then** existing VMs get the new metadata and no VM is recreated.

### DevOps user: revisions

- **Given** three template changes, **When** the user runs `kubectl rollout history`, **Then** the output lists the revisions, up to `revisionHistoryLimit`.
- **Given** a bad update, **When** the user runs `kubectl rollout undo`, **Then** the template returns to the previous revision and the rolling update starts again.

### Tenant admin: validation

- **Given** an existing set, **When** a user changes `selector`, `serviceName`, or `volumeClaimTemplates`, **Then** the webhook rejects the change.
- **Given** a selector that does not match the template labels, **When** the user creates the set, **Then** the webhook rejects it.

## Open questions

- [NEEDS CLARIFICATION: Upstream `Parallel` still limits rolling updates with `maxUnavailable`. Confirm that this spec keeps this. Owner: spec author.]
