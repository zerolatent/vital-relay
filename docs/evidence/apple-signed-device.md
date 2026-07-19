# Signed Apple device evidence

This lane instruments the real Vital Relay watchOS and iOS product paths. It does not contain a replay, a synthetic callback, or a success fallback. Every recorder session created through the public/direct `ExternalEvidenceRecorder` initializer is categorically `capture_provenance: "test_only"`, even when an XCTest bundle is hosted on physical hardware, and cannot satisfy the physical-proof gate. A simulator product artifact also fails because its `runtime` is `simulator`.

Signed physical proof is external to the repository until all of the following exist together:

- a physical Apple Watch and paired iPhone supported by the deployment targets;
- valid signing identities, provisioning profiles, app identifiers, and the Fall Detection, HealthKit, location, WatchConnectivity, and push-notification capabilities required by the product;
- a real community and responder enrollment against a reachable Vital Relay backend;
- user-granted Health/Motion and location authorization, plus actually available HealthKit/pedometer data;
- successful APNs device registration, backend registration, delivery, foreground presentation, and user open;
- a genuine `CMFallDetectionManager` callback delivered by watchOS. Never induce a fall to produce evidence; use only a naturally occurring callback or an Apple-approved supervised safety-lab procedure.

If any prerequisite or callback is absent, its canonical `stage_status` remains `not_observed`, and the verification procedure below exits nonzero.

## What the artifact can contain

The recorder accepts only a closed set of capture provenance, stage, source, receipt-status, outcome, metric-name, authorization-band, and accuracy-band enums. Cross-device identities are domain-separated SHA-256 correlations. Telemetry correlation hashes a canonical privacy-safe identity projection whose dates use the exact transported ISO-8601 representation; raw health values are excluded from the projection. The artifact cannot accept or encode raw health values, coordinates, device/APNs/access/enrollment tokens, notification bodies, user or device identifiers, or free-form diagnostic text. It also contains no prompts, model output, or hidden reasoning.

Only the launch-flag-gated product factory can issue `capture_provenance: "reviewed_product_path"`. It does so only on a physical runtime, for the exact reviewed `com.vitalrelay.app` iOS bundle or `com.vitalrelay.app.watchkitapp` watchOS bundle, and when no XCTest class, bundle/framework path, process marker, argument, or environment marker is present. It must also load or create a P-256 signing key in the physical device Secure Enclave whose opaque representation is protected by the app Keychain. If any gate or signing operation fails, product composition receives the no-op recorder.

Every product persistence signs the exact canonical session JSON and atomically writes a detached signature envelope containing the public key, its digest, the content digest, and the DER ECDSA signature. Restart accepts a product session only when the SHA sidecar, canonical schema, current Keychain/Secure Enclave public key, and signature all agree. Test construction is `authenticity.mode: "unsigned_test_only"`; software test signatures are explicitly labeled and cannot pass the live gate.

This is a device-key signature, not Apple or Secure Enclave attestation. The exported public key and signature prove integrity relative to that key, but do not independently prove Secure Enclave origin, app identity, installed-binary identity, or Apple signing. Before collecting final evidence, the trusted host pins each observed device public-key digest in a separately signed enrollment record. The final verifier accepts only those independently enrolled pins; the artifact's own `authenticity.public_key_sha256` is never its trust anchor. The procedure also binds the pins, source, signed-build metadata, install/run records, and captures in a trusted-host-signed operator record. Its verification is meaningful only when the reviewer obtains the trusted-host public key through an independent trusted channel.

Unit fixtures establish only parsing, closed-enum validation, restart integrity, correlation, and signature-policy behavior. They are always test-only and never count as live device evidence, even when XCTest is hosted on physical hardware.

Each process keeps an append-only observation prefix capped at 256 observations. Re-launches resume the same session. Once full, `capacity_reached` becomes `true`; old observations are never evicted, and strict verification fails. Canonical JSON uses sorted keys, and its SHA-256 and device-key signature are persisted beside it.

On every re-launch, the recorder requires the JSON and SHA files, plus the detached signature for a product session. It verifies the exact canonical JSON bytes against the strict SHA-256 sidecar before decoding and verifies the signature before accepting the session. Missing files, malformed bytes, a malformed sidecar, a stale/wrong device key, or any digest/signature mismatch fail closed: product composition receives the no-op recorder, and rejected material is never rewritten or blessed.

Evidence is disabled by default. All hooks receive the recorder through product composition, and the disabled product recorder is a no-op. Recording never authorizes, requests authorization, sends, retries, acknowledges, marks handled, triggers a handoff, or changes a product result. Persistence errors are ignored by product behavior and become an absent, stale, or digest-mismatched artifact at verification.

## Build and run the signed product

From the repository worktree that will be used to build the signed apps, require a clean tree and capture the exact source revision plus a deterministic manifest of the evidence hooks, product-path composition, bundle-identity configuration, and this procedure. All capture records are created outside the repository and must remain there:

```sh
set -euo pipefail

export VITAL_RELAY_REPOSITORY_ROOT="$(git rev-parse --show-toplevel)"
cd "$VITAL_RELAY_REPOSITORY_ROOT"
test -z "$(git status --porcelain=v1 --untracked-files=all)"

export VITAL_RELAY_EVIDENCE_OUT="$(mktemp -d)"
case "$VITAL_RELAY_EVIDENCE_OUT/" in
  "$VITAL_RELAY_REPOSITORY_ROOT/"*) exit 1 ;;
esac

: "${VITAL_RELAY_TRUSTED_HOST_PRIVATE_KEY:?set an outside-repository PEM key path}"
: "${VITAL_RELAY_TRUSTED_HOST_PUBLIC_KEY:?set its independently distributed public PEM path}"
test -f "$VITAL_RELAY_TRUSTED_HOST_PRIVATE_KEY"
test -f "$VITAL_RELAY_TRUSTED_HOST_PUBLIC_KEY"
case "$VITAL_RELAY_TRUSTED_HOST_PRIVATE_KEY" in
  "$VITAL_RELAY_REPOSITORY_ROOT/"*) exit 1 ;;
esac
case "$VITAL_RELAY_TRUSTED_HOST_PUBLIC_KEY" in
  "$VITAL_RELAY_REPOSITORY_ROOT/"*) exit 1 ;;
esac
openssl pkey -in "$VITAL_RELAY_TRUSTED_HOST_PRIVATE_KEY" -pubout \
  | cmp - "$VITAL_RELAY_TRUSTED_HOST_PUBLIC_KEY"

git rev-parse HEAD > "$VITAL_RELAY_EVIDENCE_OUT/source-revision.txt"

VITAL_RELAY_SOURCE_MANIFEST="$VITAL_RELAY_EVIDENCE_OUT/source-manifest.txt"
for source in \
  apps/apple/Sources/VitalRelayFeature/AppleFallEventCoordinator.swift \
  apps/apple/Sources/VitalRelayFeature/WatchHealthTelemetryConsumer.swift \
  apps/apple/Sources/VitalRelayWatchTransport/ExternalEvidenceContracts.swift \
  apps/apple/Sources/VitalRelayWatchTransport/ExternalEvidenceRecorder.swift \
  apps/apple/Sources/VitalRelayWatchTransport/WatchMessageEnvelope.swift \
  apps/apple/Sources/VitalRelayWatchTransport/WatchConnectivityTransport.swift \
  apps/apple/VitalRelayApp/VitalRelayAppRouter.swift \
  apps/apple/VitalRelayApp/VitalRelayPushAppDelegate.swift \
  apps/apple/VitalRelayWatchApp/WatchFallDetectionController.swift \
  apps/apple/VitalRelayWatchApp/WatchHealthTelemetryController.swift \
  apps/apple/VitalRelay.xcodeproj/project.pbxproj \
  apps/apple/VitalRelay-Info.plist \
  apps/apple/VitalRelayWatchApp/VitalRelayWatch-Info.plist \
  docs/evidence/apple-signed-device.md
do
  test -f "$source"
  shasum -a 256 "$source"
done > "$VITAL_RELAY_SOURCE_MANIFEST"

shasum -a 256 "$VITAL_RELAY_SOURCE_MANIFEST" \
  | awk '{print $1}' \
  > "$VITAL_RELAY_EVIDENCE_OUT/source-manifest.sha256"

swift test --package-path apps/apple

xcodebuild \
  -project apps/apple/VitalRelay.xcodeproj \
  -scheme VitalRelay \
  -configuration Debug \
  -destination 'generic/platform=iOS' \
  CODE_SIGNING_ALLOWED=NO \
  CODE_SIGNING_REQUIRED=NO \
  build

xcodebuild \
  -project apps/apple/VitalRelay.xcodeproj \
  -scheme VitalRelayWatchApp \
  -configuration Debug \
  -destination 'generic/platform=watchOS' \
  CODE_SIGNING_ALLOWED=NO \
  CODE_SIGNING_REQUIRED=NO \
  build
```

This works after integration because it records the revision actually built rather than assuming a particular parent shape. The Git revision binds the clean repository state; the retained manifest makes the reviewed evidence and bundle-identity files independently checkable. Neither record proves which binary was installed or how it was signed.

Use Xcode with the real team, entitlements, and profiles to install the `VitalRelay` app and embedded `VitalRelayWatchApp` on a fresh paired-device installation. Do not weaken entitlements or replace device callbacks with debug buttons. Launch both installed apps with the evidence flag:

```sh
export VITAL_RELAY_IPHONE='<CoreDevice identifier or device name>'
export VITAL_RELAY_WATCH='<CoreDevice identifier or device name>'
: "${VITAL_RELAY_SIGNED_IPHONE_APP:?set the locally built signed iOS .app path}"
: "${VITAL_RELAY_SIGNED_WATCH_APP:?set the locally built signed watchOS .app path}"

test -n "$VITAL_RELAY_IPHONE"
test -n "$VITAL_RELAY_WATCH"
test -d "$VITAL_RELAY_SIGNED_IPHONE_APP"
test -d "$VITAL_RELAY_SIGNED_WATCH_APP"

codesign --verify --deep --strict "$VITAL_RELAY_SIGNED_IPHONE_APP"
codesign --verify --deep --strict "$VITAL_RELAY_SIGNED_WATCH_APP"
codesign -d --verbose=4 "$VITAL_RELAY_SIGNED_IPHONE_APP" \
  > "$VITAL_RELAY_EVIDENCE_OUT/iphone-codesign.txt" 2>&1
codesign -d --entitlements :- "$VITAL_RELAY_SIGNED_IPHONE_APP" \
  >> "$VITAL_RELAY_EVIDENCE_OUT/iphone-codesign.txt" 2>&1
codesign -d --verbose=4 "$VITAL_RELAY_SIGNED_WATCH_APP" \
  > "$VITAL_RELAY_EVIDENCE_OUT/watch-codesign.txt" 2>&1
codesign -d --entitlements :- "$VITAL_RELAY_SIGNED_WATCH_APP" \
  >> "$VITAL_RELAY_EVIDENCE_OUT/watch-codesign.txt" 2>&1

privacy_safe_devicectl_record() {
  raw="$1"
  destination="$2"
  selected_device="$3"
  jq -cS --arg selected_device "$selected_device" '
    walk(
      if type == "object" then
        with_entries(select(
          (.key | test("^(deviceIdentifier|deviceName|udid|ecid|serialNumber|dnsName)$"; "i"))
          | not
        ))
      elif type == "string" and . == $selected_device then
        "[redacted-device-selector]"
      else . end
    )
  ' "$raw" > "$destination"
  rm -f -- "$raw"
}

xcrun devicectl device info apps \
  --device "$VITAL_RELAY_IPHONE" \
  --bundle-id com.vitalrelay.app \
  --json-output "$VITAL_RELAY_EVIDENCE_OUT/.iphone-install.raw.json"
privacy_safe_devicectl_record \
  "$VITAL_RELAY_EVIDENCE_OUT/.iphone-install.raw.json" \
  "$VITAL_RELAY_EVIDENCE_OUT/iphone-install.json" \
  "$VITAL_RELAY_IPHONE"

xcrun devicectl device info apps \
  --device "$VITAL_RELAY_WATCH" \
  --bundle-id com.vitalrelay.app.watchkitapp \
  --json-output "$VITAL_RELAY_EVIDENCE_OUT/.watch-install.raw.json"
privacy_safe_devicectl_record \
  "$VITAL_RELAY_EVIDENCE_OUT/.watch-install.raw.json" \
  "$VITAL_RELAY_EVIDENCE_OUT/watch-install.json" \
  "$VITAL_RELAY_WATCH"

xcrun devicectl device process launch \
  --device "$VITAL_RELAY_IPHONE" \
  --terminate-existing \
  --json-output "$VITAL_RELAY_EVIDENCE_OUT/.iphone-launch.raw.json" \
  com.vitalrelay.app \
  -VitalRelayExternalEvidenceEnabled YES
privacy_safe_devicectl_record \
  "$VITAL_RELAY_EVIDENCE_OUT/.iphone-launch.raw.json" \
  "$VITAL_RELAY_EVIDENCE_OUT/iphone-launch.json" \
  "$VITAL_RELAY_IPHONE"

xcrun devicectl device process launch \
  --device "$VITAL_RELAY_WATCH" \
  --terminate-existing \
  --json-output "$VITAL_RELAY_EVIDENCE_OUT/.watch-launch.raw.json" \
  com.vitalrelay.app.watchkitapp \
  -VitalRelayExternalEvidenceEnabled YES
privacy_safe_devicectl_record \
  "$VITAL_RELAY_EVIDENCE_OUT/.watch-launch.raw.json" \
  "$VITAL_RELAY_EVIDENCE_OUT/watch-launch.json" \
  "$VITAL_RELAY_WATCH"

for suffix in json sha256 signature.json
do
  xcrun devicectl device copy from \
    --device "$VITAL_RELAY_IPHONE" \
    --domain-type appDataContainer \
    --domain-identifier com.vitalrelay.app \
    --source "Library/Application Support/VitalRelayWatchTransport/apple-external-evidence-session.$suffix" \
    --destination "$VITAL_RELAY_EVIDENCE_OUT/iphone.enrollment.$suffix"
  xcrun devicectl device copy from \
    --device "$VITAL_RELAY_WATCH" \
    --domain-type appDataContainer \
    --domain-identifier com.vitalrelay.app.watchkitapp \
    --source "Library/Application Support/VitalRelayWatchTransport/apple-external-evidence-session.$suffix" \
    --destination "$VITAL_RELAY_EVIDENCE_OUT/watch.enrollment.$suffix"
done

validate_enrollment_candidate() {
  artifact="$1"
  sidecar="$2"
  signature_envelope="$3"
  scratch_prefix="$4"
  expected_platform="$5"
  canonical="$artifact.canonical"
  envelope_canonical="$signature_envelope.canonical"
  public_der="$VITAL_RELAY_EVIDENCE_OUT/$scratch_prefix.enrollment.public.der"
  public_pem="$VITAL_RELAY_EVIDENCE_OUT/$scratch_prefix.enrollment.public.pem"
  signature_der="$VITAL_RELAY_EVIDENCE_OUT/$scratch_prefix.enrollment.signature.der"

  jq -cS . "$artifact" | tr -d '\n' > "$canonical"
  cmp "$canonical" "$artifact"
  content_sha256="$(shasum -a 256 "$artifact" | awk '{print $1}')"
  test "$(tr -d '[:space:]' < "$sidecar")" = "$content_sha256"
  jq -cS . "$signature_envelope" | tr -d '\n' > "$envelope_canonical"
  cmp "$envelope_canonical" "$signature_envelope"
  jq -e --arg content_sha256 "$content_sha256" '
    keys == [
      "algorithm", "key_origin", "public_key_der_base64",
      "public_key_sha256", "schema_version", "signature_der_base64",
      "signed_content_sha256"
    ] and
    .schema_version == 1 and
    .algorithm == "p256_ecdsa_sha256" and
    .key_origin == "secure_enclave_keychain" and
    .signed_content_sha256 == $content_sha256 and
    (.public_key_sha256 | test("^[0-9a-f]{64}$")) and
    (.public_key_der_base64 | type == "string" and length > 0) and
    (.signature_der_base64 | type == "string" and length > 0)
  ' "$signature_envelope" >/dev/null
  candidate_key_sha256="$(jq -er '.public_key_sha256' "$signature_envelope")"
  jq -e \
    --arg candidate_key_sha256 "$candidate_key_sha256" \
    --arg expected_platform "$expected_platform" '
    .schema_version == 3 and
    .platform == $expected_platform and
    .capture_provenance == "reviewed_product_path" and
    .runtime == "physical_device" and
    .authenticity.mode == "secure_enclave_device_key" and
    .authenticity.signature_algorithm == "p256_ecdsa_sha256" and
    .authenticity.public_key_sha256 == $candidate_key_sha256
  ' "$artifact" >/dev/null
  jq -er '.public_key_der_base64' "$signature_envelope" \
    | base64 -D > "$public_der"
  jq -er '.signature_der_base64' "$signature_envelope" \
    | base64 -D > "$signature_der"
  test "$(shasum -a 256 "$public_der" | awk '{print $1}')" \
    = "$candidate_key_sha256"
  openssl pkey -pubin -inform DER -in "$public_der" -out "$public_pem"
  openssl dgst -sha256 -verify "$public_pem" \
    -signature "$signature_der" "$artifact"
  rm -f -- "$canonical" "$envelope_canonical" \
    "$public_der" "$public_pem" "$signature_der"
}

validate_enrollment_candidate \
  "$VITAL_RELAY_EVIDENCE_OUT/iphone.enrollment.json" \
  "$VITAL_RELAY_EVIDENCE_OUT/iphone.enrollment.sha256" \
  "$VITAL_RELAY_EVIDENCE_OUT/iphone.enrollment.signature.json" \
  iphone ios
validate_enrollment_candidate \
  "$VITAL_RELAY_EVIDENCE_OUT/watch.enrollment.json" \
  "$VITAL_RELAY_EVIDENCE_OUT/watch.enrollment.sha256" \
  "$VITAL_RELAY_EVIDENCE_OUT/watch.enrollment.signature.json" \
  watch watchos

PINNED_IPHONE_DEVICE_KEY_SHA256="$(
  jq -er '.public_key_sha256 | select(test("^[0-9a-f]{64}$"))' \
    "$VITAL_RELAY_EVIDENCE_OUT/iphone.enrollment.signature.json"
)"
PINNED_WATCH_DEVICE_KEY_SHA256="$(
  jq -er '.public_key_sha256 | select(test("^[0-9a-f]{64}$"))' \
    "$VITAL_RELAY_EVIDENCE_OUT/watch.enrollment.signature.json"
)"
test "$PINNED_IPHONE_DEVICE_KEY_SHA256" != "$PINNED_WATCH_DEVICE_KEY_SHA256"
ENROLLMENT_PROVENANCE_MANIFEST="$VITAL_RELAY_EVIDENCE_OUT/enrollment-provenance-manifest.txt"
(
  cd "$VITAL_RELAY_EVIDENCE_OUT"
  for record in \
    source-revision.txt source-manifest.txt source-manifest.sha256 \
    iphone-codesign.txt iphone-install.json iphone-launch.json \
    iphone.enrollment.json iphone.enrollment.sha256 iphone.enrollment.signature.json \
    watch-codesign.txt watch-install.json watch-launch.json \
    watch.enrollment.json watch.enrollment.sha256 watch.enrollment.signature.json
  do
    shasum -a 256 "$record"
  done
) > "$ENROLLMENT_PROVENANCE_MANIFEST"
ENROLLMENT_PROVENANCE_SHA256="$(
  shasum -a 256 "$ENROLLMENT_PROVENANCE_MANIFEST" | awk '{print $1}'
)"
TRUSTED_HOST_PUBLIC_KEY_SHA256="$(
  openssl pkey -pubin -in "$VITAL_RELAY_TRUSTED_HOST_PUBLIC_KEY" -outform DER \
    | shasum -a 256 \
    | awk '{print $1}'
)"
DEVICE_KEY_ENROLLMENT="$VITAL_RELAY_EVIDENCE_OUT/device-key-enrollment.json"

jq -cnS \
  --arg iphone_key "$PINNED_IPHONE_DEVICE_KEY_SHA256" \
  --arg watch_key "$PINNED_WATCH_DEVICE_KEY_SHA256" \
  --arg provenance "$ENROLLMENT_PROVENANCE_SHA256" \
  --arg host_key "$TRUSTED_HOST_PUBLIC_KEY_SHA256" \
  '{
    record_type: "vital_relay_device_key_enrollment",
    schema_version: 1,
    iphone_pinned_device_key_sha256: $iphone_key,
    watch_pinned_device_key_sha256: $watch_key,
    enrollment_provenance_manifest_sha256: $provenance,
    trusted_host_public_key_sha256: $host_key,
    continuity_claim: "trusted_host_pinned_device_key",
    apple_or_secure_enclave_attestation: false
  }' | tr -d '\n' > "$DEVICE_KEY_ENROLLMENT"
openssl dgst -sha256 \
  -sign "$VITAL_RELAY_TRUSTED_HOST_PRIVATE_KEY" \
  -out "$VITAL_RELAY_EVIDENCE_OUT/device-key-enrollment.signature.der" \
  "$DEVICE_KEY_ENROLLMENT"
openssl dgst -sha256 \
  -verify "$VITAL_RELAY_TRUSTED_HOST_PUBLIC_KEY" \
  -signature "$VITAL_RELAY_EVIDENCE_OUT/device-key-enrollment.signature.der" \
  "$DEVICE_KEY_ENROLLMENT"
```

The trusted-host-signed enrollment record is the independent key pin; neither the final artifact nor its signature envelope may replace it. Retain it, its signature, its provenance manifest, and the trusted-host public key separately. The enrollment proves continuity from the host-observed key, not Apple or Secure Enclave attestation. Now perform the visible Health/Motion start action and allow real source data to arrive. Exercise the authenticated backend ingestion path. Deliver a real APNs responder invitation while the app is foregrounded, observe its presentation, and open it. Exercise the real emergency fall path only under the safety constraint above, allowing the Watch durable outbox, iPhone inbox/backend handoff, iPhone handled state, and Watch acknowledgement to finish.

## Collect without committing evidence

Copy both JSON files and their digest sidecars into the same outside-repository evidence directory created above:

```sh
test -d "$VITAL_RELAY_EVIDENCE_OUT"
test -f "$VITAL_RELAY_EVIDENCE_OUT/source-revision.txt"
test -f "$VITAL_RELAY_EVIDENCE_OUT/source-manifest.txt"
test -f "$VITAL_RELAY_EVIDENCE_OUT/source-manifest.sha256"
test -f "$VITAL_RELAY_EVIDENCE_OUT/device-key-enrollment.json"
test -f "$VITAL_RELAY_EVIDENCE_OUT/device-key-enrollment.signature.der"
test -f "$VITAL_RELAY_EVIDENCE_OUT/enrollment-provenance-manifest.txt"
for record in \
  iphone-codesign.txt iphone-install.json iphone-launch.json \
  watch-codesign.txt watch-install.json watch-launch.json
do
  test -s "$VITAL_RELAY_EVIDENCE_OUT/$record"
done

xcrun devicectl device copy from \
  --device "$VITAL_RELAY_IPHONE" \
  --domain-type appDataContainer \
  --domain-identifier com.vitalrelay.app \
  --source 'Library/Application Support/VitalRelayWatchTransport/apple-external-evidence-session.json' \
  --destination "$VITAL_RELAY_EVIDENCE_OUT/iphone.json"

xcrun devicectl device copy from \
  --device "$VITAL_RELAY_IPHONE" \
  --domain-type appDataContainer \
  --domain-identifier com.vitalrelay.app \
  --source 'Library/Application Support/VitalRelayWatchTransport/apple-external-evidence-session.sha256' \
  --destination "$VITAL_RELAY_EVIDENCE_OUT/iphone.sha256"

xcrun devicectl device copy from \
  --device "$VITAL_RELAY_IPHONE" \
  --domain-type appDataContainer \
  --domain-identifier com.vitalrelay.app \
  --source 'Library/Application Support/VitalRelayWatchTransport/apple-external-evidence-session.signature.json' \
  --destination "$VITAL_RELAY_EVIDENCE_OUT/iphone.signature.json"

xcrun devicectl device copy from \
  --device "$VITAL_RELAY_WATCH" \
  --domain-type appDataContainer \
  --domain-identifier com.vitalrelay.app.watchkitapp \
  --source 'Library/Application Support/VitalRelayWatchTransport/apple-external-evidence-session.json' \
  --destination "$VITAL_RELAY_EVIDENCE_OUT/watch.json"

xcrun devicectl device copy from \
  --device "$VITAL_RELAY_WATCH" \
  --domain-type appDataContainer \
  --domain-identifier com.vitalrelay.app.watchkitapp \
  --source 'Library/Application Support/VitalRelayWatchTransport/apple-external-evidence-session.sha256' \
  --destination "$VITAL_RELAY_EVIDENCE_OUT/watch.sha256"

xcrun devicectl device copy from \
  --device "$VITAL_RELAY_WATCH" \
  --domain-type appDataContainer \
  --domain-identifier com.vitalrelay.app.watchkitapp \
  --source 'Library/Application Support/VitalRelayWatchTransport/apple-external-evidence-session.signature.json' \
  --destination "$VITAL_RELAY_EVIDENCE_OUT/watch.signature.json"
```

Missing devices, containers, files, or disabled instrumentation make these commands fail nonzero. Never copy the app container wholesale: it can contain credentials and product data. Never commit captured device artifacts.

## Verify canonical proof and correlation

This verifier requires `jq`, OpenSSL, and the macOS `shasum`, plus the independently distributed trusted-host public key. It verifies the host-signed enrollment before reading either device pin, and it never derives a trust anchor from a final artifact. It stops on the first missing or wrong pin, replaced device key, missing prerequisite, digest mismatch, invalid schema/stage semantics, truncated session, unobserved stage, or broken correlation. On success it writes content-addressed copies named by their exact SHA-256 outside the repository.

```sh
set -euo pipefail

: "${VITAL_RELAY_EVIDENCE_OUT:?set the outside-repository evidence directory}"
test -d "$VITAL_RELAY_EVIDENCE_OUT"
case "$VITAL_RELAY_EVIDENCE_OUT" in
  /*) ;;
  *) exit 1 ;;
esac
VITAL_RELAY_REPOSITORY_ROOT="$(git rev-parse --show-toplevel)"
case "$VITAL_RELAY_EVIDENCE_OUT/" in
  "$VITAL_RELAY_REPOSITORY_ROOT/"*) exit 1 ;;
esac

IPHONE_JSON="$VITAL_RELAY_EVIDENCE_OUT/iphone.json"
WATCH_JSON="$VITAL_RELAY_EVIDENCE_OUT/watch.json"
IPHONE_SIGNATURE="$VITAL_RELAY_EVIDENCE_OUT/iphone.signature.json"
WATCH_SIGNATURE="$VITAL_RELAY_EVIDENCE_OUT/watch.signature.json"
SOURCE_REVISION_FILE="$VITAL_RELAY_EVIDENCE_OUT/source-revision.txt"
SOURCE_MANIFEST="$VITAL_RELAY_EVIDENCE_OUT/source-manifest.txt"
SOURCE_MANIFEST_DIGEST="$VITAL_RELAY_EVIDENCE_OUT/source-manifest.sha256"
DEVICE_KEY_ENROLLMENT="$VITAL_RELAY_EVIDENCE_OUT/device-key-enrollment.json"
DEVICE_KEY_ENROLLMENT_SIGNATURE="$VITAL_RELAY_EVIDENCE_OUT/device-key-enrollment.signature.der"
ENROLLMENT_PROVENANCE_MANIFEST="$VITAL_RELAY_EVIDENCE_OUT/enrollment-provenance-manifest.txt"
: "${VITAL_RELAY_TRUSTED_HOST_PUBLIC_KEY:?set the independently distributed public PEM path}"
test -s "$VITAL_RELAY_TRUSTED_HOST_PUBLIC_KEY"
case "$VITAL_RELAY_TRUSTED_HOST_PUBLIC_KEY" in
  /*) ;;
  *) exit 1 ;;
esac
case "$VITAL_RELAY_TRUSTED_HOST_PUBLIC_KEY" in
  "$VITAL_RELAY_REPOSITORY_ROOT/"*|"$VITAL_RELAY_EVIDENCE_OUT/"*) exit 1 ;;
esac

verify_source_capture() {
  test -s "$SOURCE_REVISION_FILE"
  test -s "$SOURCE_MANIFEST"
  test -s "$SOURCE_MANIFEST_DIGEST"
  recorded_revision="$(tr -d '[:space:]' < "$SOURCE_REVISION_FILE")"
  printf '%s\n' "$recorded_revision" | grep -Eq '^[0-9a-f]{40}$'
  expected_manifest_digest="$(tr -d '[:space:]' < "$SOURCE_MANIFEST_DIGEST")"
  actual_manifest_digest="$(shasum -a 256 "$SOURCE_MANIFEST" | awk '{print $1}')"
  test "$expected_manifest_digest" = "$actual_manifest_digest"

  VITAL_RELAY_REPOSITORY_ROOT="$(git rev-parse --show-toplevel)"
  cd "$VITAL_RELAY_REPOSITORY_ROOT"
  test -z "$(git status --porcelain=v1 --untracked-files=all)"
  test "$(git rev-parse HEAD)" = "$recorded_revision"
  shasum -a 256 -c "$SOURCE_MANIFEST"
}

verify_device_key_enrollment() {
  test -s "$DEVICE_KEY_ENROLLMENT"
  test -s "$DEVICE_KEY_ENROLLMENT_SIGNATURE"
  test -s "$ENROLLMENT_PROVENANCE_MANIFEST"
  enrollment_canonical="$DEVICE_KEY_ENROLLMENT.canonical"
  jq -cS . "$DEVICE_KEY_ENROLLMENT" | tr -d '\n' > "$enrollment_canonical"
  cmp "$enrollment_canonical" "$DEVICE_KEY_ENROLLMENT"

  trusted_host_public_key_sha256="$(
    openssl pkey -pubin -in "$VITAL_RELAY_TRUSTED_HOST_PUBLIC_KEY" -outform DER \
      | shasum -a 256 \
      | awk '{print $1}'
  )"
  enrollment_provenance_sha256="$(
    shasum -a 256 "$ENROLLMENT_PROVENANCE_MANIFEST" | awk '{print $1}'
  )"
  jq -e \
    --arg host_key "$trusted_host_public_key_sha256" \
    --arg provenance "$enrollment_provenance_sha256" '
      keys == [
        "apple_or_secure_enclave_attestation", "continuity_claim",
        "enrollment_provenance_manifest_sha256",
        "iphone_pinned_device_key_sha256", "record_type", "schema_version",
        "trusted_host_public_key_sha256", "watch_pinned_device_key_sha256"
      ] and
      .record_type == "vital_relay_device_key_enrollment" and
      .schema_version == 1 and
      .continuity_claim == "trusted_host_pinned_device_key" and
      .apple_or_secure_enclave_attestation == false and
      .trusted_host_public_key_sha256 == $host_key and
      .enrollment_provenance_manifest_sha256 == $provenance and
      (.iphone_pinned_device_key_sha256 | test("^[0-9a-f]{64}$")) and
      (.watch_pinned_device_key_sha256 | test("^[0-9a-f]{64}$")) and
      .iphone_pinned_device_key_sha256 != .watch_pinned_device_key_sha256
    ' "$DEVICE_KEY_ENROLLMENT" >/dev/null
  openssl dgst -sha256 \
    -verify "$VITAL_RELAY_TRUSTED_HOST_PUBLIC_KEY" \
    -signature "$DEVICE_KEY_ENROLLMENT_SIGNATURE" \
    "$DEVICE_KEY_ENROLLMENT"
  (
    cd "$VITAL_RELAY_EVIDENCE_OUT"
    shasum -a 256 -c "$(basename "$ENROLLMENT_PROVENANCE_MANIFEST")"
  )
  rm -f -- "$enrollment_canonical"
}

verify_digest() {
  artifact="$1"
  sidecar="$2"
  canonical="$artifact.canonical"
  jq -cS . "$artifact" | tr -d '\n' > "$canonical"
  cmp "$canonical" "$artifact"
  expected="$(tr -d '[:space:]' < "$sidecar")"
  actual="$(shasum -a 256 "$artifact" | awk '{print $1}')"
  test "$expected" = "$actual"
  jq -e '
    ([.. | objects | keys[]] |
      all(. != "value" and
          . != "latitude" and
          . != "longitude" and
          . != "device_token" and
          . != "access_token" and
          . != "enrollment_token" and
          . != "notification_body" and
          . != "user_id" and
          . != "device_id"))
  ' "$artifact" >/dev/null
  cp "$artifact" "$VITAL_RELAY_EVIDENCE_OUT/$actual.json"
}

verify_device_key_signature() {
  artifact="$1"
  signature_envelope="$2"
  scratch_prefix="$3"
  pinned_public_key_sha256="$4"
  printf '%s\n' "$pinned_public_key_sha256" | grep -Eq '^[0-9a-f]{64}$'
  envelope_canonical="$signature_envelope.canonical"
  jq -cS . "$signature_envelope" | tr -d '\n' > "$envelope_canonical"
  cmp "$envelope_canonical" "$signature_envelope"

  content_sha256="$(shasum -a 256 "$artifact" | awk '{print $1}')"
  public_key_sha256="$(jq -er '.public_key_sha256' "$signature_envelope")"
  jq -e --arg content_sha256 "$content_sha256" '
    keys == [
      "algorithm", "key_origin", "public_key_der_base64",
      "public_key_sha256", "schema_version", "signature_der_base64",
      "signed_content_sha256"
    ] and
    .schema_version == 1 and
    .algorithm == "p256_ecdsa_sha256" and
    .key_origin == "secure_enclave_keychain" and
    .signed_content_sha256 == $content_sha256 and
    (.public_key_sha256 | test("^[0-9a-f]{64}$")) and
    (.public_key_der_base64 | type == "string" and length > 0) and
    (.signature_der_base64 | type == "string" and length > 0)
  ' "$signature_envelope" >/dev/null
  test "$public_key_sha256" = "$pinned_public_key_sha256"
  test "$(jq -er '.authenticity.public_key_sha256' "$artifact")" \
    = "$pinned_public_key_sha256"

  public_der="$VITAL_RELAY_EVIDENCE_OUT/$scratch_prefix.public.der"
  public_pem="$VITAL_RELAY_EVIDENCE_OUT/$scratch_prefix.public.pem"
  signature_der="$VITAL_RELAY_EVIDENCE_OUT/$scratch_prefix.signature.der"
  jq -er '.public_key_der_base64' "$signature_envelope" \
    | base64 -D > "$public_der"
  jq -er '.signature_der_base64' "$signature_envelope" \
    | base64 -D > "$signature_der"
  test "$(shasum -a 256 "$public_der" | awk '{print $1}')" \
    = "$public_key_sha256"
  openssl pkey -pubin -inform DER -in "$public_der" -out "$public_pem"
  openssl dgst -sha256 -verify "$public_pem" \
    -signature "$signature_der" "$artifact"
  rm -f -- "$public_der" "$public_pem" "$signature_der"
}

validate_session_semantics() {
  artifact="$1"
  expected_platform="$2"
  jq -e --arg expected_platform "$expected_platform" '
    def sha256: type == "string" and test("^[0-9a-f]{64}$");
    def integer: type == "number" and . == floor;
    [
      "evidence_session_started",
      "watch_connectivity_callback_registered",
      "fall_detection_callback_registered",
      "fall_detection_callback_received",
      "watch_critical_outbox_durable",
      "iphone_critical_event_received",
      "watch_critical_acknowledgement_received",
      "iphone_critical_event_handled",
      "watch_health_source_started",
      "watch_health_metric_names_available",
      "iphone_telemetry_callback_registered",
      "iphone_telemetry_received",
      "health_ingestion_receipt",
      "location_authorization_observed",
      "location_accuracy_band_observed",
      "apns_callback_registered",
      "apns_authorization_granted",
      "apns_device_registration_succeeded",
      "apns_backend_registration_receipt",
      "apns_notification_presented",
      "apns_notification_opened",
      "emergency_handoff_decoded",
      "emergency_handoff_routing_started",
      "emergency_backend_receipt",
      "emergency_handoff_completed"
    ] as $stages |
    [
      "heartRate", "restingHeartRate", "walkingHeartRateAverage",
      "heartRateVariabilitySDNN", "heartRateRecoveryOneMinute",
      "atrialFibrillationBurden", "oxygenSaturation", "respiratoryRate",
      "wristTemperature", "sleepingBreathingDisturbances", "stepCount",
      "activeEnergyBurned", "basalEnergyBurned", "walkingRunningDistance",
      "cyclingDistance", "swimmingDistance", "wheelchairDistance",
      "flightsClimbed", "walkingSpeed", "walkingStepLength",
      "walkingAsymmetryPercentage", "walkingDoubleSupportPercentage",
      "stairAscentSpeed", "stairDescentSpeed", "walkingSteadiness"
    ] as $metrics |
    [
      "fall_detection", "watch_connectivity", "healthkit_workout",
      "pedometer", "health_metric_api", "health_capability_api",
      "core_location", "apns", "notification_handoff", "apple_fall_backend"
    ] as $sources |
    [
      "evidence_session_started", "watch_connectivity_callback_registered",
      "fall_detection_callback_registered", "fall_detection_callback_received",
      "watch_critical_outbox_durable", "watch_critical_acknowledgement_received",
      "watch_health_source_started", "watch_health_metric_names_available"
    ] as $watch_observation_stages |
    [
      "evidence_session_started", "watch_connectivity_callback_registered",
      "iphone_critical_event_received", "iphone_critical_event_handled",
      "iphone_telemetry_callback_registered", "iphone_telemetry_received",
      "health_ingestion_receipt", "location_authorization_observed",
      "location_accuracy_band_observed", "apns_callback_registered",
      "apns_authorization_granted", "apns_device_registration_succeeded",
      "apns_backend_registration_receipt", "apns_notification_presented",
      "apns_notification_opened", "emergency_handoff_decoded",
      "emergency_handoff_routing_started", "emergency_backend_receipt",
      "emergency_handoff_completed"
    ] as $iphone_observation_stages |
    def correlated: (.correlation.sha256? | sha256);
    def no_correlation: .correlation? == null;
    def no_payload_facts:
      .facts.metric_names == [] and
      .facts.receipt_status? == null and
      .facts.location_authorization_band? == null and
      .facts.location_accuracy_band? == null and
      .facts.outcome? == null;
    def source_only($source):
      .facts.source == $source and no_payload_facts;
    def no_source_or_payload:
      .facts.source? == null and no_payload_facts;
    . as $document |
    ($document | keys) == [
      "authenticity", "capacity_reached", "capture_provenance",
      "maximum_observations", "observations", "platform", "runtime",
      "schema_version", "session_id", "stage_status", "started_at_unix_ms"
    ] and
    $document.schema_version == 3 and
    $document.platform == $expected_platform and
    $document.runtime == "physical_device" and
    $document.capture_provenance == "reviewed_product_path" and
    ($document.authenticity | keys) == [
      "mode", "public_key_sha256", "signature_algorithm"
    ] and
    $document.authenticity.mode == "secure_enclave_device_key" and
    $document.authenticity.signature_algorithm == "p256_ecdsa_sha256" and
    ($document.authenticity.public_key_sha256 | sha256) and
    ($document.session_id | type == "string" and length == 36) and
    ($document.session_id |
      test("^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"; "i")) and
    ($document.started_at_unix_ms | integer) and
    ($document.maximum_observations | integer) and
    ($document.maximum_observations >= 2 and $document.maximum_observations <= 1024) and
    $document.capacity_reached == false and
    ($document.observations | type == "array" and length > 0) and
    ($document.observations | length) <= $document.maximum_observations and
    $document.observations[0].stage == "evidence_session_started" and
    $document.observations[0].observed_at_unix_ms == $document.started_at_unix_ms and
    all($document.observations | to_entries[];
      .value.sequence == (.key + 1)) and
    all($document.observations[];
      . as $observation |
      (($observation | keys) - [
        "correlation", "facts", "observed_at_unix_ms", "sequence", "stage"
      ] | length) == 0 and
      ($observation | has("facts") and has("observed_at_unix_ms") and has("sequence") and has("stage")) and
      ($observation.sequence | integer) and
      ($observation.observed_at_unix_ms | integer) and
      ($stages | index($observation.stage)) != null and
      ($observation.correlation? == null or
        (($observation.correlation | keys) == ["sha256"] and
         ($observation.correlation.sha256 | sha256))) and
      (($observation.facts | keys) - [
        "location_accuracy_band", "location_authorization_band", "metric_names",
        "outcome", "receipt_status", "source"
      ] | length) == 0 and
      ($observation.facts.metric_names | type == "array") and
      $observation.facts.metric_names == ($observation.facts.metric_names | sort | unique) and
      all($observation.facts.metric_names[];
        . as $metric | ($metrics | index($metric)) != null) and
      ($observation.facts.source? == null or
        ($sources | index($observation.facts.source)) != null) and
      ($observation.facts.receipt_status? == null or
        (["accepted", "already_processed"] | index($observation.facts.receipt_status)) != null) and
      ($observation.facts.location_authorization_band? == null or
        $observation.facts.location_authorization_band == "provider_validated") and
      ($observation.facts.location_accuracy_band? == null or
        (["under_10_meters", "10_to_50_meters", "50_to_100_meters",
          "100_to_500_meters", "over_500_meters"] |
         index($observation.facts.location_accuracy_band)) != null) and
      ($observation.facts.outcome? == null or
        (["confirmed", "dismissed", "rejected", "unresponsive", "routed", "completed"] |
         index($observation.facts.outcome)) != null)) and
    all($document.observations[];
      . as $observation |
      (($expected_platform == "watchos" and
        ($watch_observation_stages | index($observation.stage)) != null) or
       ($expected_platform == "ios" and
        ($iphone_observation_stages | index($observation.stage)) != null)) and
      ($observation |
        if .stage == "evidence_session_started" then
          no_correlation and no_source_or_payload
        elif .stage == "watch_connectivity_callback_registered" then
          no_correlation and source_only("watch_connectivity")
        elif .stage == "fall_detection_callback_registered" then
          no_correlation and source_only("fall_detection")
        elif .stage == "fall_detection_callback_received" then
          correlated and .facts.source == "fall_detection" and
          .facts.metric_names == [] and
          (["confirmed", "dismissed", "rejected", "unresponsive"] |
            index($observation.facts.outcome)) != null and
          .facts.receipt_status? == null and
          .facts.location_authorization_band? == null and
          .facts.location_accuracy_band? == null
        elif (.stage == "watch_critical_outbox_durable" or
              .stage == "watch_critical_acknowledgement_received" or
              .stage == "iphone_critical_event_received" or
              .stage == "iphone_critical_event_handled") then
          correlated and source_only("watch_connectivity")
        elif .stage == "watch_health_source_started" then
          no_correlation and
          (["healthkit_workout", "pedometer"] |
            index($observation.facts.source)) != null and
          no_payload_facts
        elif .stage == "watch_health_metric_names_available" then
          (.correlation? == null or correlated) and
          (.facts.source? == null or
            (["healthkit_workout", "pedometer", "watch_connectivity"] |
              index($observation.facts.source)) != null) and
          (.facts.metric_names | length > 0) and
          .facts.receipt_status? == null and
          .facts.location_authorization_band? == null and
          .facts.location_accuracy_band? == null and
          .facts.outcome? == null
        elif .stage == "iphone_telemetry_callback_registered" then
          no_correlation and source_only("watch_connectivity")
        elif .stage == "iphone_telemetry_received" then
          correlated and .facts.source == "watch_connectivity" and
          .facts.receipt_status? == null and
          .facts.location_authorization_band? == null and
          .facts.location_accuracy_band? == null and
          .facts.outcome? == null
        elif .stage == "health_ingestion_receipt" then
          correlated and
          (["health_metric_api", "health_capability_api"] |
            index($observation.facts.source)) != null and
          (.facts.metric_names | length > 0) and
          (["accepted", "already_processed"] |
            index($observation.facts.receipt_status)) != null and
          .facts.location_authorization_band? == null and
          .facts.location_accuracy_band? == null and
          .facts.outcome? == null
        elif .stage == "location_authorization_observed" then
          correlated and .facts.source == "core_location" and
          .facts.metric_names == [] and
          .facts.location_authorization_band == "provider_validated" and
          .facts.receipt_status? == null and
          .facts.location_accuracy_band? == null and
          .facts.outcome? == null
        elif .stage == "location_accuracy_band_observed" then
          correlated and .facts.source == "core_location" and
          .facts.metric_names == [] and
          .facts.location_accuracy_band != null and
          .facts.receipt_status? == null and
          .facts.location_authorization_band? == null and
          .facts.outcome? == null
        elif (.stage == "apns_callback_registered" or
              .stage == "apns_authorization_granted" or
              .stage == "apns_device_registration_succeeded" or
              .stage == "apns_backend_registration_receipt") then
          no_correlation and source_only("apns")
        elif (.stage == "apns_notification_presented" or
              .stage == "apns_notification_opened") then
          correlated and source_only("apns")
        elif .stage == "emergency_handoff_decoded" then
          correlated and source_only("notification_handoff")
        elif .stage == "emergency_handoff_routing_started" then
          correlated and .facts.metric_names == [] and
          .facts.receipt_status? == null and
          .facts.location_authorization_band? == null and
          .facts.location_accuracy_band? == null and
          ((.facts.source == "notification_handoff" and
            .facts.outcome? == null) or
           (.facts.source == "fall_detection" and
            (["confirmed", "dismissed", "rejected", "unresponsive"] |
              index($observation.facts.outcome)) != null))
        elif .stage == "emergency_backend_receipt" then
          correlated and .facts.source == "apple_fall_backend" and
          .facts.metric_names == [] and
          (["accepted", "already_processed"] |
            index($observation.facts.receipt_status)) != null and
          .facts.location_authorization_band? == null and
          .facts.location_accuracy_band? == null and
          .facts.outcome? == null
        elif .stage == "emergency_handoff_completed" then
          correlated and .facts.metric_names == [] and
          .facts.receipt_status? == null and
          .facts.location_authorization_band? == null and
          .facts.location_accuracy_band? == null and
          ((.facts.source == "notification_handoff" and
            .facts.outcome == "routed") or
           (.facts.source == "apple_fall_backend" and
            .facts.outcome == "completed") or
           (.facts.source == "fall_detection" and
            (["dismissed", "rejected"] |
              index($observation.facts.outcome)) != null))
        else false end)) and
    ($document.stage_status | type == "array" and length == ($stages | length)) and
    ([$document.stage_status[].stage] | sort) == ($stages | sort) and
    all($document.stage_status[];
      . as $status |
      ($status | keys) == ["observation_count", "stage", "state"] and
      ($status.observation_count | integer) and
      $status.observation_count
        == ([$document.observations[] | select(.stage == $status.stage)] | length) and
      $status.state
        == (if $status.observation_count == 0 then "not_observed" else "observed" end))
  ' "$artifact" >/dev/null
}

require_stage() {
  artifact="$1"
  stage="$2"
  jq -e --arg stage "$stage" '
    any(.stage_status[];
      .stage == $stage and
      .state == "observed" and
      .observation_count > 0)
  ' "$artifact" >/dev/null
}

correlation_for_stage() {
  artifact="$1"
  stage="$2"
  jq -er --arg stage "$stage" '
    [.observations[] |
      select(.stage == $stage and .correlation.sha256 != null) |
      .correlation.sha256][0] // error("missing correlated stage")
  ' "$artifact"
}

require_correlated_stage() {
  artifact="$1"
  stage="$2"
  correlation="$3"
  jq -e --arg stage "$stage" --arg correlation "$correlation" '
    any(.observations[];
      .stage == $stage and
      .correlation.sha256 == $correlation)
  ' "$artifact" >/dev/null
}

verify_source_capture
verify_device_key_enrollment
PINNED_IPHONE_DEVICE_KEY_SHA256="$(
  jq -er '.iphone_pinned_device_key_sha256' "$DEVICE_KEY_ENROLLMENT"
)"
PINNED_WATCH_DEVICE_KEY_SHA256="$(
  jq -er '.watch_pinned_device_key_sha256' "$DEVICE_KEY_ENROLLMENT"
)"
verify_digest "$IPHONE_JSON" "$VITAL_RELAY_EVIDENCE_OUT/iphone.sha256"
verify_digest "$WATCH_JSON" "$VITAL_RELAY_EVIDENCE_OUT/watch.sha256"
verify_device_key_signature \
  "$IPHONE_JSON" "$IPHONE_SIGNATURE" iphone \
  "$PINNED_IPHONE_DEVICE_KEY_SHA256"
verify_device_key_signature \
  "$WATCH_JSON" "$WATCH_SIGNATURE" watch \
  "$PINNED_WATCH_DEVICE_KEY_SHA256"
validate_session_semantics "$IPHONE_JSON" ios
validate_session_semantics "$WATCH_JSON" watchos

for stage in \
  evidence_session_started \
  watch_connectivity_callback_registered \
  fall_detection_callback_registered \
  fall_detection_callback_received \
  watch_critical_outbox_durable \
  watch_critical_acknowledgement_received \
  watch_health_source_started \
  watch_health_metric_names_available
do
  require_stage "$WATCH_JSON" "$stage"
done

for stage in \
  evidence_session_started \
  watch_connectivity_callback_registered \
  iphone_critical_event_received \
  iphone_critical_event_handled \
  iphone_telemetry_callback_registered \
  iphone_telemetry_received \
  health_ingestion_receipt \
  location_authorization_observed \
  location_accuracy_band_observed \
  apns_callback_registered \
  apns_authorization_granted \
  apns_device_registration_succeeded \
  apns_backend_registration_receipt \
  apns_notification_presented \
  apns_notification_opened \
  emergency_handoff_decoded \
  emergency_handoff_routing_started \
  emergency_backend_receipt \
  emergency_handoff_completed
do
  require_stage "$IPHONE_JSON" "$stage"
done

fall_correlation="$(correlation_for_stage "$WATCH_JSON" watch_critical_outbox_durable)"
require_correlated_stage "$WATCH_JSON" watch_critical_acknowledgement_received "$fall_correlation"
for stage in \
  iphone_critical_event_received \
  iphone_critical_event_handled \
  location_authorization_observed \
  location_accuracy_band_observed \
  emergency_backend_receipt \
  emergency_handoff_completed
do
  require_correlated_stage "$IPHONE_JSON" "$stage" "$fall_correlation"
done

health_correlation="$(correlation_for_stage "$WATCH_JSON" watch_health_metric_names_available)"
require_correlated_stage "$IPHONE_JSON" iphone_telemetry_received "$health_correlation"
require_correlated_stage "$IPHONE_JSON" health_ingestion_receipt "$health_correlation"

notification_correlation="$(correlation_for_stage "$IPHONE_JSON" apns_notification_opened)"
for stage in \
  apns_notification_presented \
  emergency_handoff_decoded \
  emergency_handoff_routing_started \
  emergency_handoff_completed
do
  require_correlated_stage "$IPHONE_JSON" "$stage" "$notification_correlation"
done

SOURCE_REVISION="$(tr -d '[:space:]' < "$SOURCE_REVISION_FILE")"
SOURCE_MANIFEST_SHA256="$(tr -d '[:space:]' < "$SOURCE_MANIFEST_DIGEST")"
IPHONE_EVIDENCE_SHA256="$(shasum -a 256 "$IPHONE_JSON" | awk '{print $1}')"
WATCH_EVIDENCE_SHA256="$(shasum -a 256 "$WATCH_JSON" | awk '{print $1}')"
IPHONE_SIGNATURE_SHA256="$(shasum -a 256 "$IPHONE_SIGNATURE" | awk '{print $1}')"
WATCH_SIGNATURE_SHA256="$(shasum -a 256 "$WATCH_SIGNATURE" | awk '{print $1}')"
DEVICE_KEY_ENROLLMENT_SHA256="$(shasum -a 256 "$DEVICE_KEY_ENROLLMENT" | awk '{print $1}')"
DEVICE_KEY_ENROLLMENT_SIGNATURE_SHA256="$(
  shasum -a 256 "$DEVICE_KEY_ENROLLMENT_SIGNATURE" | awk '{print $1}'
)"
ENROLLMENT_PROVENANCE_MANIFEST_SHA256="$(
  shasum -a 256 "$ENROLLMENT_PROVENANCE_MANIFEST" | awk '{print $1}'
)"
TRUSTED_HOST_PUBLIC_KEY_SHA256="$(
  openssl pkey -pubin -in "$VITAL_RELAY_TRUSTED_HOST_PUBLIC_KEY" -outform DER \
    | shasum -a 256 \
    | awk '{print $1}'
)"

HOST_PROVENANCE_MANIFEST="$VITAL_RELAY_EVIDENCE_OUT/host-provenance-manifest.txt"
(
  cd "$VITAL_RELAY_EVIDENCE_OUT"
  for record in \
    iphone-codesign.txt iphone-install.json iphone-launch.json \
    watch-codesign.txt watch-install.json watch-launch.json
  do
    shasum -a 256 "$record"
  done
) > "$HOST_PROVENANCE_MANIFEST"
HOST_PROVENANCE_MANIFEST_SHA256="$(
  shasum -a 256 "$HOST_PROVENANCE_MANIFEST" | awk '{print $1}'
)"
OPERATOR_RECORD="$VITAL_RELAY_EVIDENCE_OUT/operator-verification-record.json"
: "${VITAL_RELAY_TRUSTED_HOST_PRIVATE_KEY:?set the trusted host private PEM path}"
test -s "$VITAL_RELAY_TRUSTED_HOST_PRIVATE_KEY"
case "$VITAL_RELAY_TRUSTED_HOST_PRIVATE_KEY" in
  /*) ;;
  *) exit 1 ;;
esac
case "$VITAL_RELAY_TRUSTED_HOST_PRIVATE_KEY" in
  "$VITAL_RELAY_REPOSITORY_ROOT/"*|"$VITAL_RELAY_EVIDENCE_OUT/"*) exit 1 ;;
esac
openssl pkey -in "$VITAL_RELAY_TRUSTED_HOST_PRIVATE_KEY" -pubout \
  | cmp - "$VITAL_RELAY_TRUSTED_HOST_PUBLIC_KEY"

jq -cnS \
  --arg source_revision "$SOURCE_REVISION" \
  --arg source_manifest_sha256 "$SOURCE_MANIFEST_SHA256" \
  --arg iphone_evidence_sha256 "$IPHONE_EVIDENCE_SHA256" \
  --arg watch_evidence_sha256 "$WATCH_EVIDENCE_SHA256" \
  --arg iphone_signature_sha256 "$IPHONE_SIGNATURE_SHA256" \
  --arg watch_signature_sha256 "$WATCH_SIGNATURE_SHA256" \
  --arg iphone_device_key_sha256 "$PINNED_IPHONE_DEVICE_KEY_SHA256" \
  --arg watch_device_key_sha256 "$PINNED_WATCH_DEVICE_KEY_SHA256" \
  --arg device_key_enrollment_sha256 "$DEVICE_KEY_ENROLLMENT_SHA256" \
  --arg device_key_enrollment_signature_sha256 "$DEVICE_KEY_ENROLLMENT_SIGNATURE_SHA256" \
  --arg enrollment_provenance_manifest_sha256 "$ENROLLMENT_PROVENANCE_MANIFEST_SHA256" \
  --arg host_provenance_manifest_sha256 "$HOST_PROVENANCE_MANIFEST_SHA256" \
  --arg trusted_host_public_key_sha256 "$TRUSTED_HOST_PUBLIC_KEY_SHA256" \
  '{
    record_type: "vital_relay_apple_evidence_binding",
    schema_version: 3,
    source_revision: $source_revision,
    source_manifest_sha256: $source_manifest_sha256,
    iphone_evidence_sha256: $iphone_evidence_sha256,
    watch_evidence_sha256: $watch_evidence_sha256,
    iphone_signature_sha256: $iphone_signature_sha256,
    watch_signature_sha256: $watch_signature_sha256,
    iphone_device_key_sha256: $iphone_device_key_sha256,
    watch_device_key_sha256: $watch_device_key_sha256,
    device_key_enrollment_sha256: $device_key_enrollment_sha256,
    device_key_enrollment_signature_sha256: $device_key_enrollment_signature_sha256,
    enrollment_provenance_manifest_sha256: $enrollment_provenance_manifest_sha256,
    host_provenance_manifest_sha256: $host_provenance_manifest_sha256,
    trusted_host_public_key_sha256: $trusted_host_public_key_sha256,
    device_signature_claim: "trusted_host_pinned_device_key_continuity_not_apple_attestation",
    apple_attestation_used: false,
    device_artifacts_alone_prove_code_signing_or_device_authenticity: false
  }' | tr -d '\n' > "$OPERATOR_RECORD"

shasum -a 256 "$OPERATOR_RECORD" \
  | awk '{print $1}' \
  > "$VITAL_RELAY_EVIDENCE_OUT/operator-verification-record.sha256"

openssl dgst -sha256 \
  -sign "$VITAL_RELAY_TRUSTED_HOST_PRIVATE_KEY" \
  -out "$VITAL_RELAY_EVIDENCE_OUT/operator-verification-record.signature.der" \
  "$OPERATOR_RECORD"
openssl dgst -sha256 \
  -verify "$VITAL_RELAY_TRUSTED_HOST_PUBLIC_KEY" \
  -signature "$VITAL_RELAY_EVIDENCE_OUT/operator-verification-record.signature.der" \
  "$OPERATOR_RECORD"

OPERATOR_RECORD_SHA256="$(
  tr -d '[:space:]' < "$VITAL_RELAY_EVIDENCE_OUT/operator-verification-record.sha256"
)"
cp "$IPHONE_SIGNATURE" \
  "$VITAL_RELAY_EVIDENCE_OUT/$IPHONE_SIGNATURE_SHA256.signature.json"
cp "$WATCH_SIGNATURE" \
  "$VITAL_RELAY_EVIDENCE_OUT/$WATCH_SIGNATURE_SHA256.signature.json"
cp "$DEVICE_KEY_ENROLLMENT" \
  "$VITAL_RELAY_EVIDENCE_OUT/$DEVICE_KEY_ENROLLMENT_SHA256.device-key-enrollment.json"
cp "$OPERATOR_RECORD" \
  "$VITAL_RELAY_EVIDENCE_OUT/$OPERATOR_RECORD_SHA256.operator-verification-record.json"
```

Passing this verifier establishes that the reviewed product-path recorder produced schema-valid sessions whose exact bytes verify under the independently host-pinned device keys, and that the trusted host bound those pins and captures to the retained clean-tree source, local codesign records, privacy-redacted install/run records, and correlation checks. A missing enrollment, an untrusted host key, or a final device-key replacement fails verification. This is pinned device-key continuity; it does not establish Apple or Secure Enclave attestation and does not independently prove Secure Enclave key origin. The host signature is trustworthy only when its public key was obtained independently. Devices, profiles, authorizations, real sensor/location availability, backend identity, APNs service delivery, and safe provenance of the fall callback remain external facts that the operator must retain alongside this directory.
