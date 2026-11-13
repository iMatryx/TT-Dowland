# Retain original for three minutes

After successful automatic video delivery, retain the verified original locally
for 180 seconds so File can reuse it. Reuse does not extend the deadline; another
successful video delivery can renew it. Expiry prevents new reuse and removes
the artifact when no upload still owns it. Shutdown removes retained artifacts;
restart recovery keeps its existing behavior. Audio/photo temporary cleanup and
persistent Telegram file-id caching remain independent.

1. Add failing lease tests with a controlled monotonic clock: reuse before 180s,
   removal at expiry, active upload protection, automatic timer, close cleanup.
2. Extend SharedDownloads with opt-in retention, registry protection and expiry
   scheduling. Normal unretained leases still clean up immediately.
3. Retain only after a successful preview send; share the same original key with
   document delivery. Test immediate File avoids source download, expiry forces
   a fresh original, failures do not retain, converted preview retains original.
4. Update documentation and run the complete offline suite and focused review.

Review focus: expiry/acquire races, deadline renewal, shutdown cleanup, registry
protection, stale cache and preview/original isolation. Preserve existing dirty
checkout and do not deploy or commit in this task.

## Verification

- Lease tests verify the 180-second boundary, automatic expiry, reuse without
  deadline extension, active-upload protection, shutdown and replacement safety.
- Runtime tests verify File reuses original bytes, expiry downloads again when
  needed, failed uploads do not retain and converted previews keep the original.
- Independent review found an active original could not renew after its old
  deadline. A reproducing test failed before the identity-bound renewal fix and
  now passes; newer replacement leases remain protected.
- Final suite: 277 passed, 5 optional live tests skipped. Offline dispatcher and
  diff whitespace checks passed. No deployment or Telegram sends performed.
