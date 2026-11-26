# Serial consoles of the two images

One file per image, captured from the serial console of a QEMU boot:

| File | Image |
|---|---|
| `console-normal.log` | the image that starts the guarded service before its first client |
| `console-violated.log` | the image that starts its first client before the service exists |

Each file starts with a header written by the capture script: the host, the
emulator version, the exact `qemu-system-x86_64` command, the sha256 of the
image files, and the UTC start time. Those hashes are the same values that
`evidence/build/image.json` records, so a reader can check that the console and
the image record describe the same pair of files.

The boot host is an ordinary cloud VM (GCP e2-micro, 2 vCPU, no KVM), so QEMU
runs the guest in software emulation. The command is:

```text
qemu-system-x86_64 -cpu max -m 384 -snapshot -nographic \
  -drive file=<image>/disk-qemu,format=raw,if=ide -kernel <image>/ifs.bin
```

`-cpu max` is required: without CPUID XSAVE the QNX kernel stops early and
prints nothing. The image is a multiboot IFS, so the IFS goes in with `-kernel`
and the disk is attached beside it.

What the console holds:

- the boot of the QNX 8.0 image, including the version string that
  `evidence/build/image.json` repeats
- the `CORDON-OBS` lines the target prints, one per observed fact
- the event log the service wrote, and the slog2 dump
- `CORDON-G2-COMPLETE` at the end of the run

What it does not hold: a verdict. The target prints facts only, and the rule
for each scenario is applied on the host. To judge these two files yourself:

```bash
host/cordon run --console-normal evidence/run/console-normal.log \
    --console-negative evidence/run/console-violated.log --out out/run --gate
```

That command computes one result per scenario and exits 0 or 1. The results of
the run behind this repository are not published here: section 4.6(i) of the
QNX Development License Agreement (Non-Commercial) puts the publication of
functional evaluation results behind prior written approval from BlackBerry,
and that approval has been requested and not yet given. See the license section
of `docs/design.md`.
