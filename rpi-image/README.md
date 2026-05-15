# rpi-image — Pre-baked Buildroot images for syncbench STA

> 🛠️ **Status: early experiment.** First bootable Buildroot image landed (boots on real RPi). Syncbench agent + service are **not yet baked in** — that's the next milestone. See [Roadmap](#roadmap) below.

The goal of this folder is to ship a **single SD-card image** that, when flashed and powered on, joins the testbed as a syncbench STA without any manual `setup-linux.sh`, `uv sync`, or `systemctl enable` steps. Once complete, scaling the testbed past 10 endpoints stops being a per-device chore.

---

## What's here

| File | Size | Notes |
|---|---|---|
| `pi5-buildroot.img` | 152 MB | First hand-built Buildroot image for Raspberry Pi 5. Bootable. **Does not yet include the syncbench agent.** |

Stored via **Git LFS** — see [`.gitattributes`](../.gitattributes) at the repo root. Cloning without LFS will leave a small text pointer in place of the image.

### Image layout (`pi5-buildroot.img`)

- DOS/MBR partition table
- Partition 1: FAT32 boot (~32 MB) — RPi firmware + kernel
- Partition 2: ext4 rootfs (~120 MB) — Buildroot-generated
- Tested on: Raspberry Pi 5

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

✅ Boots on RPi 5
❌ Syncbench agent not pre-installed
❌ No first-boot config (hostname / Wi-Fi / agent_id all default)
❌ MQTT broker address not configurable from SD card
❌ Default credentials, SSH access, and security posture not yet documented

For a working STA today, use the existing flow on a stock Raspberry Pi OS image:

```bash
scripts/setup-linux.sh   # apt + Wi-Fi + uv + systemd
```

---

## Roadmap

Tracked under TARGET.md → "Goal: Buildroot 預燒 RPi Image".

- [x] Step 1 — First bootable Buildroot image
- [ ] Step 2 — Image lives in repo via LFS (this README, in progress)
- [ ] Step 3 — Buildroot config + rootfs overlay integrating the agent:
  - [ ] Buildroot defconfig committed to `rpi-image/configs/` (reproducible build)
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
