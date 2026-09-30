# Tasks: VirtualMachineStatefulSet

- **Spec**: [`spec.md`](./spec.md)
- **Plan**: [`plan.md`](./plan.md)
- **Epic**: vmop-1837

Stories vmop-1838 to vmop-1843 already existed under the epic. Stories vmop-4234 to vmop-4238 were added for the work that had no story. T032 still has a `vmop-TBD` placeholder. Replace it before the spec PR merges.

## Phase 1 — Setup

- [ ] T001 Resolve the open questions in `.sdd/specs/011-virtualmachinestatefulset/spec.md` that block Phase 2: revision hash and metadata, and `Parallel` with `maxUnavailable`.
- [ ] T002 [P] [vmop-1838] Add the `VirtualMachineStatefulSet` types in `api/v1alpha6/virtualmachinestatefulset_types.go` and the conversion stub in `api/v1alpha6/virtualmachinestatefulset_conversion.go`.
- [ ] T003 [vmop-1838] Run `make generate-go` and `make generate-manifests` to update `api/v1alpha6/zz_generated.deepcopy.go` and `config/crd/`.
- [ ] T004 [P] [vmop-1838] Register the kind in `pkg/crd/crd.go`.

## Phase 2 — Foundational

- [ ] T005 [vmop-1839] Add the typed context in `pkg/context/virtualmachinestatefulset_context.go`.
- [ ] T006 [P] [vmop-1839] Add identity helpers (names, labels, ordinals) in `pkg/statefulset/identity.go`.
- [ ] T007a [P] [vmop-4235] Add the availability helper (`Ready` `lastTransitionTime` vs `minReadySeconds`) in a shared package. Align with spec 010 so the Deployment work does not add a second copy.
- [ ] T007 [P] [vmop-4234] Add revision helpers (create, hash, history, trim, collision) in `pkg/statefulset/revision.go`.
- [ ] T008 [vmop-1839] Add the controller skeleton with the canonical reconcile loop, watches, field indexers, and RBAC markers in `controllers/virtualmachinestatefulset/virtualmachinestatefulset_controller.go`. Register it in `controllers/controllers.go` under `Features.K8sWorkloadMgmtAPI`.
- [ ] T009 [vmop-1840] Add the validation webhook in `webhooks/virtualmachinestatefulset/validation/` and the mutation webhook in `webhooks/virtualmachinestatefulset/mutation/`. Register both in `webhooks/webhooks.go` under `Features.K8sWorkloadMgmtAPI`.

## Phase 3 — User story: stable identity and storage

- [ ] T010 [US-identity] [vmop-1841] Create and adopt VMs by ordinal in `pkg/statefulset/scale.go`.
- [ ] T011 [US-storage] [vmop-1841] Create PVCs from `volumeClaimTemplates` and attach them to VMs in `pkg/statefulset/storage.go`.
- [ ] T012 [US-storage] [vmop-1841] Add the PVC retention policy and PVC owner references with `MergeFromWithOptimisticLock` in `pkg/statefulset/storage.go`.
- [ ] T013 [P] [US-identity] [vmop-1841] Unit tests in `controllers/virtualmachinestatefulset/virtualmachinestatefulset_controller_test.go` and `pkg/statefulset/statefulset_test.go`.

## Phase 4 — User story: ordering

- [ ] T014 [US-ordering] [vmop-1842] Add `OrderedReady` scale up and down in `pkg/statefulset/scale.go`.
- [ ] T015 [US-ordering] [vmop-1842] Add `Parallel` scale up and down in `pkg/statefulset/scale.go`.
- [ ] T016 [P] [US-ordering] [vmop-1842] Unit tests for both policies in `pkg/statefulset/statefulset_test.go`.

## Phase 5 — User story: updates and revisions

- [ ] T017 [US-updates] [vmop-1843] Add `RollingUpdate` with `partition` and `maxUnavailable` in `pkg/statefulset/update.go`.
- [ ] T018 [US-updates] [vmop-1843] Add `OnDelete` in `pkg/statefulset/update.go`.
- [ ] T019 [US-updates] [vmop-1842] Propagate template metadata in place, with no rollout, in `pkg/statefulset/update.go`.
- [ ] T020 [US-revisions] [vmop-4234] Trim history to `revisionHistoryLimit` and support rollback in `pkg/statefulset/revision.go`.
- [ ] T021 [US-updates] [vmop-1843] Write status counts, revisions, `observedGeneration`, and conditions in `controllers/virtualmachinestatefulset/virtualmachinestatefulset_controller.go`.
- [ ] T022 [P] [US-updates] [vmop-1843] Unit tests in `pkg/statefulset/statefulset_test.go`.

## Phase 6 — Validation and integration

- [ ] T023 [US-validation] [vmop-1840] Webhook rules: selector match, immutable fields, volume name collisions in `webhooks/virtualmachinestatefulset/validation/virtualmachinestatefulset_validator.go`.
- [ ] T024 [P] [US-validation] [vmop-1840] Webhook tests in `webhooks/virtualmachinestatefulset/validation/virtualmachinestatefulset_validator_test.go`.
- [ ] T025 [P] [vmop-4236] Envtest integration tests with `testlabels.EnvTest` in `controllers/virtualmachinestatefulset/virtualmachinestatefulset_controller_test.go`.

## Phase 7 — E2E

- [ ] T026 [vmop-4237] E2E: stable identity and storage in `test/e2e/vmservice/virtualmachinestatefulset/`. Skip with `skipper.SkipUnlessK8sWorkloadMgmtAPIIsEnabled`.
- [ ] T027 [P] [vmop-4237] E2E: ordering for both policies in `test/e2e/vmservice/virtualmachinestatefulset/`.
- [ ] T028 [P] [vmop-4237] E2E: `RollingUpdate`, `OnDelete`, `partition`, `maxUnavailable`, and `kubectl rollout history` and `undo` in `test/e2e/vmservice/virtualmachinestatefulset/`.
- [ ] T029 [P] [vmop-4237] E2E: PVC retention for `whenScaled` and `whenDeleted` in `test/e2e/vmservice/virtualmachinestatefulset/`.

## Phase Final — Polish

- [ ] T030 [vmop-4238] Add the user guide in `docs/` and the release note.
- [ ] T031 Set the epic in `spec.md`, `plan.md`, `tasks.md`, and `.sdd/INDEX.md`. Set the status to `Implemented` in the last PR.
- [ ] T032 [vmop-TBD] Remove the `K8sWorkloadMgmtAPI` gate for this kind when the feature is GA. Track this with the ReplicaSet gate in a separate spec.
