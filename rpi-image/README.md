# rpi-image — Pre-baked Buildroot images for syncbench STA

> 🛠️ **Status: early experiment.** v3 image (2026-05-16) boots on real RPi 5 with an observability-enabled custom kernel + openssh login. Syncbench agent + service are **not yet baked in** — that's the next milestone. See [Roadmap](#roadmap) below.

The goal of this folder is to ship a **single SD-card image** that, when flashed and powered on, joins the testbed as a syncbench STA without any manual `setup-linux.sh`, `uv sync`, or `systemctl enable` steps. Once complete, scaling the testbed past 10 endpoints stops being a per-device chore.

---

## What's here

| File | Size | Notes |
|---|---|---|
| `pi5-buildroot.img` | 152 MB | Hand-built Buildroot image for Raspberry Pi 5 (v3, 2026-05-16). Bootable, ssh-enabled, observability kernel. **Does not yet include the syncbench agent.** |

Stored via **Git LFS** — see [`.gitattributes`](../.gitattributes) at the repo root. Cloning without LFS will leave a small text pointer in place of the image.

### Image layout (`pi5-buildroot.img`)

- DOS/MBR partition table
- Partition 1: FAT32 boot (~32 MB) — RPi firmware + kernel
- Partition 2: ext4 rootfs (~120 MB) — Buildroot-generated
- Tested on: Raspberry Pi 5 (BCM2712)

---

## Flashing

### Option A — Raspberry Pi Imager (recommended for first time)

1. Open Raspberry Pi Imager → "Choose OS" → "Use custom" → pick `pi5-buildroot.img`
2. Choose your SD card → Write
3. Insert into RPi 5 → power on

### Option B — `dd` (macOS / Linux)

⚠️ **Read the device path carefully — `dd` to the wrong disk wipes it.**

```bash
# macOS — find the SD card device
diskutil list

# Replace /dev/diskN with your actual SD card (rdisk is faster on macOS)
diskutil unmountDisk /dev/diskN
sudo dd if=pi5-buildroot.img of=/dev/rdiskN bs=4m status=progress
sync
diskutil eject /dev/diskN
```

```bash
# Linux — find the SD card device
lsblk

# Replace /dev/sdX with your SD card
sudo dd if=pi5-buildroot.img of=/dev/sdX bs=4M status=progress conv=fsync
sudo eject /dev/sdX
```

---

## What's working / not working today

✅ Boots on RPi 5 (BCM2712)
✅ `eth0` auto-DHCP on boot — find the IP from your router or `arp -a`
✅ openssh built in — `ssh root@<pi-ip>` works out of the box (see [Default credentials](#default-credentials))
✅ Observability-enabled kernel — DWARF5 + BTF, ftrace (irqsoff / preempt), histogram triggers, user/uprobe events, lock stats → eBPF CO-RE ready
✅ Full `vim` (with runtime) for on-box editing
❌ Syncbench agent not pre-installed
❌ Wi-Fi STA join not configured (eth0 only for now)
❌ No first-boot config (hostname / agent_id all default)
❌ MQTT broker address not configurable from SD card

For a working syncbench STA today, use the existing flow on a stock Raspberry Pi OS image:

```bash
scripts/setup-linux.sh   # apt + Wi-Fi + uv + systemd
```

### Default credentials

| Field | Value |
|---|---|
| User | `root` |
| Password | `pi5` (sha-512 hashed in `/etc/shadow`) |
| SSH | enabled, `PermitRootLogin yes` (patched via `post-build.sh`) |

⚠️ **These defaults are for lab-only use.** Change the password on first login (`passwd`) or rebuild the image with your own before deploying anywhere shared.

### Kernel observability features

| Feature | Why it's on |
|---|---|
| `CONFIG_DEBUG_INFO_DWARF5` + `CONFIG_DEBUG_INFO_BTF` | eBPF CO-RE foundation; `pahole` encodes DWARF → BTF so portable bpf programs work without per-kernel rebuilds |
| `CONFIG_HIST_TRIGGERS` | In-kernel histograms over tracepoints / events (latency distributions without userspace post-processing) |
| `CONFIG_USER_EVENTS` + `CONFIG_UPROBE_EVENTS` | User-space probes for app-level tracing |
| `CONFIG_IRQSOFF_TRACER` + `CONFIG_PREEMPT_TRACER` | IRQ/preemption-off latency tracing — useful when chasing tail-latency on a soft-realtime STA |
| `CONFIG_LOCK_STAT` | Lock contention stats (`/proc/lock_stat`) for kernel-side bottleneck diagnosis |

These come from `board/raspberrypi/linux-observability.fragment` (21 lines), wired into the Buildroot defconfig as a kernel config fragment.

---

## Roadmap

Tracked under TARGET.md → "Goal: Buildroot 預燒 RPi Image".

- [x] Step 1 — First bootable Buildroot image
- [x] Step 2 — Image lives in repo via LFS (this README)
- [~] Step 3 — Buildroot config + rootfs overlay integrating the agent (in progress, v3 is the kernel + ssh baseline):
  - [x] Kernel customization workflow (fragment + `make savedefconfig` → defconfig persistence)
  - [x] Observability kernel features baked in (see [Kernel observability features](#kernel-observability-features))
  - [x] openssh + root login (`post-build.sh` patches `sshd_config`)
  - [ ] Buildroot defconfig + overlay committed to `rpi-image/configs/` (currently lives outside the repo — needs to come in for reproducible build)
  - [ ] Rootfs overlay with `atf-agent` code (or pre-built wheels)
  - [ ] Pre-installed: `iperf3`, Python runtime, `uv` (or wheel-based deploy)
  - [ ] `atf-agent.service` enabled by default
- [ ] Step 4 — First-boot config injection (no per-device image rebuild):
  - [ ] hostname / `agent_id` from a SD-card config file
  - [ ] Wi-Fi SSID/PSK from a SD-card config file
  - [ ] MQTT broker address + InfluxDB token from a SD-card config file
- [ ] Step 5 — End-to-end validation:
  - [ ] Flash a fresh SD → boot → controller sees the agent online with zero manual steps
  - [ ] Document time-to-onboard vs. the manual `setup-linux.sh` flow

---

## Why Buildroot (vs. a Raspberry Pi OS overlay)

- **Reproducibility** — every byte of the rootfs is deterministic from the defconfig + overlay; no "works on my SD card" surprises across STAs
- **Boot time** — Buildroot rootfs boots in seconds, not minutes; matters when 10+ STAs reboot during a scenario
- **Image size** — current image is 152 MB; a stock Raspberry Pi OS image is ~2 GB
- **Auditability** — only what we put in is in there, useful when the testbed is part of a measurement methodology that needs to be defended

Trade-off: Buildroot's learning curve is real (this image is the first one I built end-to-end). The integration work in Steps 3–5 is the bulk of the remaining effort.
