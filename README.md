# Device Lab: Jenkins + Real Android Devices at Home

**by Fikret (Ryan) Ture**

A small, realistic test lab built the way a QA team sets one up at work: a laptop for writing tests, a headless Linux server for Jenkins, a test machine with the web portal and fake devices, and real Android devices connected over Wi-Fi.

Every setup step lives in a script in this repo, so a new server can be prepared with one command instead of by memory (Infrastructure as Code).

---

## Architecture

| Machine | Role at a company | What runs on it |
|---|---|---|
| Mac mini | Engineer's work laptop | Writes tests, pushes to GitHub |
| Surface Book 2 (Ubuntu Server, no desktop, dead batteries, always on the charger) | Jenkins server | Jenkins controller (needs no GPU) |
| Surface Book (1st gen, Ubuntu Server) | Test lab (staging environment) | Web portal, 6 simulated devices (systemd service), Jenkins agent |
| Second Surface Book 2 (healthy batteries) | Local AI server | Ollama on the GTX 1060; healthy batteries cover GPU power peaks, 95 W charger |
| Android tablet + phone | Real test devices | Connected to the lab with adb over Wi-Fi |

Flow: push to GitHub → Jenkins sees the change → Jenkins sends the job to the agent → the agent runs the tests on the portal, the simulated devices and the real devices → results go back to Jenkins.

---

## Scripts

| Script | What it does |
|---|---|
| `scripts/setup-wifi.sh` | Connects Ubuntu Server to Wi-Fi with netplan. No YAML editing by hand. Asks for the Wi-Fi name and password, backs up conflicting installer files, writes the config with the right indentation and `600` permissions, applies it and prints the IP address. |
| `scripts/setup-console.sh` | Makes the text console readable on high-resolution screens (largest Terminus font, optional lower console resolution) and stops kernel info messages from flooding the console. |
| `scripts/setup-surface-gpu.sh` | Makes the NVIDIA GPU in a Surface Book 2 usable on Ubuntu Server (linux-surface kernel + NVIDIA 580 server driver), for running local AI models on the GPU. Tested: `nvidia-smi` shows the GTX 1060 with 6 GB, CUDA 13.0. |
| `scripts/setup-server-power.sh` | Keeps a laptop server running with the lid closed and never sleeping (logind lid settings + masked sleep targets). |
| `scripts/setup-power-saving.sh` | Installs TLP so a laptop server uses less power on battery and goes back to normal on the charger. |
| `scripts/setup-xtts.sh` | RyanAI Voice: Coqui XTTS v2 text to speech (Turkish, English and more) as a systemd service on port 8020 with its own paste-and-download page; the RyanAI app uses it for Read aloud. Pins PyTorch and torchaudio 2.11 + CUDA 12.6 (CUDA 12.8+ builds dropped Pascal GPUs; torchaudio cu126 stops at 2.11). Non-commercial license: run with `--agree-coqui-license`. |
| `scripts/setup-chatterbox.sh` | Adds Chatterbox Multilingual (MIT license, Turkish) as a second RyanAI Voice engine in its own venv on 127.0.0.1:8021. The page's Engine menu switches between XTTS and Chatterbox; only one engine stays on the GPU at a time. |
| `scripts/setup-jenkins.sh` | Jenkins LTS from the official apt repository, Java 21, systemd service on port 8080 (installed on the AI server). Refuses to run if 8080 is taken. Home network only, no port forwarding. |
| `scripts/setup-jenkins-agent.sh` | Makes the test-lab a Jenkins SSH agent: Java 21, git, adb, Python venv, a `jenkins` user that logs in only with the controller's key. The agent runs the tests next to the devices; the controller only schedules them and keeps the results. |
| `scripts/setup-ryanai-app.sh` | RyanAI, our own chat app on port 8090: accounts (each user sees only their chats), streaming answers, Auto model routing straight to Ollama (no hidden prompts or background jobs), thinking block, image input, read aloud via RyanAI Voice. Users: `sudo ryanai-user adduser <name>`. |
| `scripts/setup-test-lab.sh` | Turns an Ubuntu Server machine into the test lab: Python venv, portal + 6 simulated devices as a systemd service that starts at boot and restarts on failure. |
| `scripts/setup-jenkins.sh` | Coming next: installs Java 21 and Jenkins LTS from the official Jenkins repository. |

---

## Setting up the Jenkins server from zero

### 1. Install Ubuntu Server

1. Download the latest **Ubuntu Server LTS** ISO from ubuntu.com and verify it:
   ```bash
   grep "live-server-amd64" SHA256SUMS | shasum -a 256 -c
   ```
2. Write it to a USB stick with balenaEtcher.
3. Boot the laptop from the stick (Surface: hold **Volume Down**, press **Power**).
4. In the installer: choose **Ubuntu Server** (not minimized), tick **Search for third-party drivers**, and tick **Install OpenSSH server**.

> Use a **USB flash drive**, not a bus-powered external hard disk. On the Surface Book, a 2 TB USB hard disk did not get enough power to boot the installer ("can't open blockdev" errors). An 8 GB flash drive worked right away.

### 2. Connect to Wi-Fi

If Wi-Fi did not work during the install, copy `setup-wifi.sh` to a USB stick, plug it into the server, and run:

```bash
lsblk                                  # find the stick, for example sdb1
sudo mount /dev/sdb1 /mnt
sudo bash /mnt/setup-wifi.sh
```

If the server already has a network, clone this repo and run `sudo ./scripts/setup-wifi.sh`.

### 3. Work from your laptop over SSH

```bash
ssh <user>@<server-ip>
```

From here on, everything is copy and paste from the laptop. Nobody needs to sit at the server.

### 4. Make the console readable (optional)

```bash
sudo ./scripts/setup-console.sh            # large font
sudo ./scripts/setup-console.sh 1500x1000  # large font + lower resolution, then reboot
```

---

## Android devices over Wi-Fi

```bash
# One time, per device: Settings > Developer options > Wireless debugging > Pair device with pairing code
adb pair <device-ip>:<pairing-port>
adb connect <device-ip>:<port>

# Switch to a fixed port, so the address never changes until the device reboots
adb -s <device-ip>:<port> tcpip 5555
adb connect <device-ip>:5555           # wait 3 seconds after tcpip before connecting

# Keep the screen on (tablets in a lab are always on charge)
adb -s <device-ip>:5555 shell settings put system screen_off_timeout 2147483647
adb -s <device-ip>:5555 shell settings get system screen_off_timeout   # verify
```

Then reserve every device's IP address in the router (DHCP reservation), so the lab addresses stay the same forever.

---

## Troubleshooting (real problems from building this lab)

| Symptom | Cause | Fix |
|---|---|---|
| `Unable to find a medium containing a live file system` / `can't open blockdev` while booting the installer | Bus-powered USB hard disk does not get enough power, or spins up too slowly | Use a USB flash drive. For slow disks, `rootdelay=30` on the boot line can help. |
| Installer stuck on `DHCPv4` for Wi-Fi | Wrong Wi-Fi password, or Wi-Fi card not ready | Continue without network, then run `setup-wifi.sh` after the install |
| `netplan apply`: error at `8:11` | YAML indentation or a missing `:` on the line **above** the reported line | Use `setup-wifi.sh`, it writes the YAML for you |
| `networkd backend does not support wifi with match` | The installer left `/etc/netplan/00-installer-config.yaml` that matches Wi-Fi by MAC address. `sudo netplan get` shows a `macaddress` that is not in your file. | Move that file out of `/etc/netplan` (`setup-wifi.sh` does this and keeps a backup) |
| Console floods with `dptf_power` messages (Surface), and they come back after reboot | Intel power driver messages; `dmesg -n 1` is temporary | `setup-console.sh`: sets `kernel.printk` permanently and blacklists `dptf_power` |
| Console floods with `nouveau` errors (NVIDIA GPU, Surface Book) | Open-source NVIDIA driver does not handle the detachable GPU well | `setup-console.sh` blacklists `nouveau` and adds `quiet loglevel=3` to the boot line. The proprietary NVIDIA driver comes later, only if the GPU is needed. |
| `lspci` shows no NVIDIA GPU on a Surface Book 2 | The stock Ubuntu kernel does not bring up the GPU in the keyboard base | Install the linux-surface kernel and reboot (`setup-surface-gpu.sh`); the GTX 1060 then appears in `lspci` |
| NVIDIA driver choice for GTX 10xx | Pascal GPUs are not supported by the `-open` kernel modules, and 580 is the last branch that supports them | Use `nvidia-headless-580-server` + `nvidia-utils-580-server` (no desktop parts on a server) |
| Ollama shows `100% CPU`; Ollama debug log says `cuInit failed: 999`; kernel log shows `pciehp: Slot(4): Link Down` and `NVRM: Xid 79, GPU has fallen off the bus` when CUDA starts | Surface Book 2 15" with a **60 W** charger and both batteries at 0% "Not charging". The GTX 1060 draws more than the charger can give, and the empty batteries cannot cover the peak, so the GPU drops off the PCIe bus. Runtime PM off, persistence mode and `pcie_aspm=off` stopped the idle drop cycle but not the drop under load. | Use the original **95 W** (or 127 W) Surface charger on the keyboard base so the batteries charge and the GPU gets enough power. Until then, run models on the CPU. How it was found: `ollama ps` → `OLLAMA_DEBUG=1 ollama serve` → `dmesg` → `/sys/class/power_supply`. |
| `BAT*/status` stays `Not charging` at 0% even with the charger online (`ADP1/online` = 1); `BAT*/uevent` shows the base battery at about 4 V (design 11.36 V) and the screen battery at 0 V | The Li-ion packs are over-discharged below a safe voltage, so the charge controller refuses to charge them. The clipboard will not detach either (red light), because its own battery cannot power it. | Do not try to force-revive over-discharged lithium packs (heat and swelling risk). Use the machine on wall power for a job that needs no GPU peaks (here: the Jenkins controller) and move the GPU work to a machine with healthy batteries. |
| `df -h /` shows about 100 GB on a much bigger disk | The Ubuntu Server installer gives the root LVM volume only 100 GB and leaves the rest of the disk unused (`sudo vgs` shows it under VFree) | `sudo lvextend -r -l +100%FREE /dev/ubuntu-vg/ubuntu-lv` grows the volume and the file system online, no reboot. On the AI server: 98 GB to 466 GB. |
| Browser on the Mac says "unreachable" for `http://<lab-ip>:8000`, but `curl` from Terminal works | macOS Local Network privacy: the browser has no permission to reach devices on the home network (localhost still works) | System Settings > Privacy & Security > Local Network > enable the browser, then restart the browser |
| After `Ctrl+C` and starting the systemd service, a device answers but its `uptime_s` is older than the service | The manually started copy kept running in the background; the service copy could not bind the port | Reboot (or kill the old process). "Got an answer" is not the same as "the right process answered": check uptime. |
| Android app says `Cannot reach server` | Read the app log: `adb logcat -d -s <TAG>`. In this lab the log showed the app calling `10.0.2.2` (the emulator default) because the Server URL setting was never saved. | Save the correct server URL. Also logged as a bug: the app shows the same message for every failure, which hides the real cause. |

---

## RyanAI: local chat on the AI server

RyanAI is our own chat app (`ai/ryanai_app.py` + `ai/ryanai_ui.html`), installed with `scripts/setup-ryanai-app.sh` as the `ryanai-app` service on port 8090. It talks to Ollama directly, so each request carries only the conversation itself: no hidden system prompts and no background title or tag jobs queued in front of the answer.

```bash
sudo bash scripts/setup-ryanai-app.sh
sudo ryanai-user adduser <name> --admin   # leave out --admin for other users
```

Open `http://<ai-server-ip>:8090`. There is no sign-up page; accounts are made only with `ryanai-user`.

**Auto** picks the model for each message: images go to the vision model, code and error messages to the code model, long or "analyze / step by step" requests think on the big model, everything else goes to the fast model. If a model fails, a fallback model answers. Each answer shows the model, the mode and the time it took. The context size is set once for all models with `OLLAMA_CONTEXT_LENGTH=8192` in a systemd drop-in for ollama.service.

Open WebUI was used first and removed: its hidden instructions (about 7000 tokens) and background jobs made a simple answer take over a minute on this GPU.

Remote access: no port forwarding. Use a private network (Tailscale) or Cloudflare Tunnel with Cloudflare Access in front.

---

## Security notes

- **No port forwarding.** adb on port 5555 must never be reachable from the internet: anyone could install apps on the device or read its data. Everything stays on the local network.
- **No secrets in the repo.** `setup-wifi.sh` asks for the Wi-Fi password at run time. The config file it writes is readable only by root (`600`).
- **The Jenkins server is headless and managed over SSH**, the same way production servers are.

---

## License

MIT
