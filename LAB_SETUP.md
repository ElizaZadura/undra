# Agent Lab Box — Setup Runbook

Fresh install through network isolation, for a box that will host successive
agent projects with meaningful autonomy.

**Do phases 0–4 with the monitor attached.** A firewall mistake is trivial at the
console and painful over SSH.

**Goal state:** the box reaches the internet freely, cannot start conversations
with anything else in your home, and you can still log into it.

> **Revision note.** Phase 4's rules changed after the first draft. The old
> version said "allow anything that isn't the ethernet port", which would have
> waved WiFi — or any interface added later — straight past the block. If you
> have an older copy of this file, don't apply its firewall section.

---

## A five-minute primer on the network bits

Skip if this is familiar. It's here so the rest of the document isn't guesswork.

**Your home network** is `192.168.1.something`. Every device gets one of those
numbers. Written as `192.168.1.0/24`, which just means "the range `192.168.1.1`
through `192.168.1.254`". The `/24` is shorthand for how much of the address is
the *network* part versus the *device* part — no need to care beyond copying it
correctly.

**The router is `192.168.1.1`.** It's both the way out to the internet and a
computer in its own right, with an admin page that can change your WiFi password.
That second fact is why we don't want the agent box able to reach it.

**The gateway** is just "the thing you send internet traffic to" — your router.
Same number, different job.

**DHCP** is the router handing out addresses automatically. Convenient, but the
address can change. **Static** means we pick one and it never moves — better for
a server you want to find reliably.

**DNS** translates `google.com` into a number. By default your router does this.
We point at `1.1.1.1` instead — partly speed, but mainly because Phase 4 blocks
the box from talking to the router, and a box that can't do DNS looks broken in a
very confusing way.

**Interface** = a network port. `eno1` is the ethernet socket. `wlx...` is WiFi.
`lo` is the machine talking to itself. Docker invents more of them.

---

## Phase 0 — BIOS (monitor attached)

| Setting | Value | Why |
|---|---|---|
| Restore AC Power Loss | **Power On** | Otherwise a power blip means the box stays off until you're physically there. Most important setting here. |
| Wake-on-LAN / PCIe Power On | Enabled | Lets you wake it remotely if it ever goes down. |
| CSM / Launch CSM | **Disabled** | With CSM on, the board hunts for a legacy boot sector and never sees the UEFI bootloader — you get dropped to BIOS or PXE with no `ubuntu` entry in the boot list. |
| Secure Boot → OS Type | Other OS | ASUS's way of *not* enforcing Secure Boot. This is the permissive setting; leave it. |
| Network / PXE boot | Disabled | Stops the board wasting time on it. |
| Fan profile | Silent | It'll idle at near-zero load for weeks. |

WiFi isn't in this table. On this box it's a Realtek RTL8811AU on an internal USB
lane rather than a PCIe card, so it may have no BIOS toggle at all. Phase 2.5
handles it from Linux, which is more reliable anyway.

---

## Phase 1 — Install

**Ubuntu Server LTS**, minimal install, no desktop.

- **Wipe the whole disk**, take **LVM**. Say no to encryption — it means typing a
  passphrase at every boot, which defeats a headless box that must come back up
  on its own after a power cut.
- **No featured server snaps.** None of them. The Docker snap in particular is
  confined and auto-updates on Canonical's schedule; an agent box restarting its
  container runtime at 4am is a failure you don't want. Phase 3 installs it from
  Docker's own repo.
- **Install OpenSSH server** — yes, and import your GitHub keys while you're
  there. That also disables password auth automatically.

### Network screen

Edit **`eno1`** — the ethernet one. IPv4 Method → **Manual**:

| Field | Value |
|---|---|
| Subnet | `192.168.1.0/24` |
| Address | `192.168.1.240` |
| Gateway | `192.168.1.1` |
| Name servers | `1.1.1.1, 9.9.9.9` |
| Search domains | *leave blank* |

Leave the `wlx...` entry alone.

**Why `.240`:** the RT-AC68U hands out addresses from `.2` upward, so there's no
"outside the pool" to pick. A high number won't collide unless you own 200+
devices. Shrink the router's pool to end at `.199` later if you want it strictly
correct.

### LVM sizing

The installer's LVM default allocates roughly **half the disk** to root, holding
the rest back for snapshots. If you'd rather have the space, either bump the root
volume at the partition summary, or expand later on a live system:

```bash
sudo lvextend -l +100%FREE /dev/ubuntu-vg/ubuntu-lv
sudo resize2fs /dev/ubuntu-vg/ubuntu-lv
```

---

## Phase 2 — Base setup

### 2.1 Confirm the network

The installer writes `/etc/netplan/00-installer-config.yaml` with everything you
entered. **Don't add a second netplan file** — netplan merges everything in that
directory, and two files both declaring a default route produces
`Conflicting default route declarations`. Edit the installer's file if you need
changes.

```bash
ip -br addr          # eno1 → 192.168.1.240/24
resolvectl status | grep -A2 'Current DNS'
```

DNS must be `1.1.1.1`, not `192.168.1.1`. Fix it before Phase 4.

One change worth making — set `dhcp6: false` in the installer's file, since
Phase 2.2 disables IPv6 anyway:

```bash
sudo nano /etc/netplan/00-installer-config.yaml
sudo netplan apply
```

The file pins the interface with `match: macaddress` and `set-name: eno1`, so the
name stays stable regardless of what the kernel calls it at boot.

### 2.2 Turn IPv6 off

Two addressing systems means two sets of firewall rules. This box needs one.

```bash
echo 'net.ipv6.conf.all.disable_ipv6=1
net.ipv6.conf.default.disable_ipv6=1' | sudo tee /etc/sysctl.d/99-no-ipv6.conf
sudo sysctl --system
```

### 2.3 Time

Ubuntu Server ships **chrony**, not `systemd-timesyncd` — so
`timedatectl show-timesync` errors out. That's expected, not a problem.

```bash
timedatectl                    # want: synchronized yes, NTP service active
chronyc sources
```

Anything other than your router in that list is fine. `Reach 377` means the last
eight polls all succeeded.

**Leave the timezone on UTC.** DST is genuinely awkward for scheduled work: on
the October changeover 02:00–03:00 happens twice locally, so anything scheduled
in that hour runs twice; in March it doesn't exist and gets skipped. Log
timestamps inherit the same ambiguity. The scripts all use UTC explicitly, and
`invariants.toml` carries `timezone_human` for anything that needs presenting in
local time.

For a quick local reading: `TZ=Europe/Stockholm date`.

### 2.4 SSH keys

If you imported GitHub keys at install, password auth is already off and your key
is already in `authorized_keys`. Confirm:

```bash
sudo sshd -T | grep -i passwordauthentication      # want: no
```

From your **main machine**, verify key login works:

```powershell
ssh -i $env:USERPROFILE\.ssh\YOURKEY elz@192.168.1.240 "echo ok"
```

**Windows gotcha:** if you get `WARNING: UNPROTECTED PRIVATE KEY FILE`, the key
is readable by other accounts and OpenSSH refuses it:

```powershell
$key = "$env:USERPROFILE\.ssh\YOURKEY"
icacls $key /inheritance:r
icacls $key /grant:r "${env:USERNAME}:R"
```

Worth running `icacls` on every private key in `~/.ssh` and checking which groups
have access — sandbox groups from other tooling can inherit read access to your
credentials from the profile directory.

Save yourself the `-i` flag forever, in `C:\Users\you\.ssh\config`:

```
Host red
    HostName 192.168.1.240
    User elz
    IdentityFile ~/.ssh/YOURKEY
```

If you ever need to push a key manually — Windows has no real `ssh-copy-id`:

```powershell
type $env:USERPROFILE\.ssh\YOURKEY.pub | ssh elz@192.168.1.240 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys"
```

### 2.5 Automatic security updates

```bash
sudo apt update && sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

No `ufw` — it fights with Docker in ways that are hard to debug, and Phase 4
covers what matters. Nothing is forwarded to this box from the internet.

### 2.6 Kill the WiFi radio

```bash
sudo rfkill block wlan
sudo systemctl enable systemd-rfkill
rfkill list                        # want: Soft blocked: yes
```

Optional now rather than load-bearing — the revised Phase 4 rules filter every
interface except a named list, so a stray WiFi connection would be blocked
anyway. Still worth switching off an unused radio on an unattended box.

---

## Phase 3 — Docker and project layout

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker
```

**One network per project** — the structural version of the charter's "never
touch anything outside your assigned scope". Project two cannot reach project
one's ledger because there is no route between them.

```bash
docker network create --driver bridge undra
```

```
/srv/lab/
  undra/
    compose.yml
    ledger.db
    CHARTER.md  invariants.toml  situation_report.py  publish_log.py
    docs/       reports/
  <next-project>/
```

Keep the host boring. Everything project-specific lives in a container with its
own network, volume and credentials.

---

## Phase 4 — Network isolation (the part that matters)

### What we're doing, plainly

The box should reach the internet but nothing *at home* — not the router's admin
page, not your laptop, not a printer.

Everything on your home network starts with `192.168.`, so we block traffic
addressed to that range.

**This does not block the internet, and the reason is worth understanding.** When
the box talks to `1.1.1.1`, the packet is *addressed to* `1.1.1.1` and merely
*handed to* the router on its way out. The address on the envelope is what we
filter, and it isn't a `192.168.` one. Only traffic whose destination really is a
device in your home gets stopped. This is the most counterintuitive part of the
setup, and the first `curl` afterwards looks alarming if you haven't got it
straight.

Logging in from your laptop still works: we only filter conversations the box
*starts*. Replies to something you initiated are recognised as part of an
existing conversation.

### The rules

```bash
sudo nano /etc/nftables.conf
```

Replace the entire contents:

```nft
#!/usr/sbin/nft -f

# NOTE: deliberately no "flush ruleset" — that would wipe Docker's own rules
# every time this reloads and silently break container networking. The
# create-then-delete idiom below makes this file safe to re-apply any time.

table inet agentlab
delete table inet agentlab

table inet agentlab {
    # "Everything in my house." Blocking traffic addressed here is the whole job.
    set lan4 {
        type ipv4_addr
        flags interval
        elements = {
            10.0.0.0/8,
            172.16.0.0/12,
            192.168.0.0/16,
            169.254.0.0/16      # link-local, incl. cloud metadata endpoints
        }
    }

    set lan6 {
        type ipv6_addr
        flags interval
        elements = { fe80::/10, fc00::/7 }
    }

    # Traffic the host machine itself starts.
    chain output {
        type filter hook output priority 10; policy accept;

        # Replies to conversations someone else started. Keeps your SSH alive.
        ct state established,related accept

        # Interfaces internal by nature: the machine talking to itself, Docker's
        # bridges, the Tailscale tunnel. None of these is the way out to the LAN.
        oifname { "lo", "docker0", "tailscale0" } accept
        oifname "br-*" accept

        # Everything else headed for a home address: stop, and count it.
        ip  daddr @lan4 counter log prefix "lab-out4 " drop
        ip6 daddr @lan6 counter log prefix "lab-out6 " drop
    }

    # Traffic containers start, passing through the host on its way out.
    chain forward {
        type filter hook forward priority 10; policy accept;
        ct state established,related accept

        # Containers must not reach your Tailscale network either.
        oifname "tailscale0" counter log prefix "lab-tailnet " drop

        oifname { "docker0" } accept
        oifname "br-*" accept

        ip  daddr @lan4 counter log prefix "lab-fwd4 " drop
        ip6 daddr @lan6 counter log prefix "lab-fwd6 " drop
    }
}
```

```bash
sudo nft -f /etc/nftables.conf
sudo systemctl enable --now nftables
```

**If anything goes wrong** — instant undo, leaves Docker alone:

```bash
sudo nft delete table inet agentlab
```

### Why the exemptions are listed by name

The first draft said "allow anything that isn't the ethernet port". That's wrong
in a way that matters: it would have waved WiFi straight through, along with any
interface appearing later — a second NIC, a VPN, a USB tether.

Naming the exemptions inverts the default. Anything unexpected gets filtered
rather than trusted, and the rules work unchanged whether the box is on ethernet
or WiFi.

The `br-*` line is needed because Docker's bridges live at `172.17.0.0` and up,
inside the blocked `172.16.0.0/12` range. Without it, containers couldn't reach
each other.

### Check it worked

```bash
curl -s https://api.ipify.org && echo              # internet: works
curl -m 3 -sS http://192.168.1.1 ; echo "rc=$?"    # router: should fail
ping -c1 -W2 192.168.1.10                          # your laptop: should fail
```

From a container — the test that actually matters:

```bash
docker run --rm --network undra alpine sh -c \
  'ping -c1 -W2 1.1.1.1 && (ping -c1 -W2 192.168.1.1 || echo "LAN blocked: good")'
```

From your **main machine**, confirm you still have a way in:

```bash
ssh red 'uptime'
```

### The counters are a tripwire

```bash
sudo nft list ruleset | grep counter
```

They start at zero. If they climb, something on that box went looking for your
home network — exactly the signal you want from an autonomous agent. Worth wiring
into the daily digest.

---

## Phase 5 — The router question

Phase 4 built a wall **on the box**. It works, but the box enforces it, so it
holds as long as the box is behaving. A wall enforced by the **router** can't be
removed by the box. That's the entire difference — insurance against a
compromised machine, not a fix for anything Phase 4 missed.

**Your RT-AC68U:** guest WiFi isolation is real (*Access Intranet: Disable*), but
it can't separate the wired LAN ports — all four are one bridge and stock
firmware has no per-port VLAN.

**Recommendation: use the cable and skip this.** For a box running unattended for
two weeks, ethernet beats WiFi; a drop at 3am with nobody home is a dead night of
cycles. The scenario the guest network guards against is remote when the only
things running are your own agents behind Phase 4's rules.

<details>
<summary>If you want the extra layer anyway</summary>

1. Guest Network → **5GHz** → **Access Intranet: Disable**
2. **Access time: Unlimited** — the default is often time-limited and would
   silently cut the box off mid-run
3. Connect to that SSID, leave ethernet unplugged

**Tailscale becomes mandatory** — guest clients aren't reachable from your LAN
either, so local SSH stops working. Do Phase 6 before unplugging the monitor.

**Switch back to DHCP** — the guest network does its own addressing. Set
`dhcp4: true`, drop `addresses` and `routes`, **keep `nameservers`**. Then add
this to the `output` chain right after the `ct state` line, or the box loses its
address when the lease renews:

```nft
        udp dport { 67, 68 } accept     # DHCP renewal
```

The rules need no other change — they don't care which interface is the way out.
</details>

---

## Phase 6 — Remote access

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh
```

Phase 4 already stops containers reaching `tailscale0`, so agents can't wander
onto your tailnet even though the host is on it.

---

## Done checklist

- [ ] BIOS: restore-on-power-loss **on**, CSM **disabled**
- [ ] `ip -br addr` shows `192.168.1.240`
- [ ] `resolvectl status` shows 1.1.1.1, not the router
- [ ] `timedatectl` shows synchronized, chrony not pointing at the router
- [ ] `sshd -T` shows `passwordauthentication no`
- [ ] `ssh red 'echo ok'` works from the main machine
- [ ] `rfkill list` shows wlan soft blocked
- [ ] `nft list ruleset` shows the `agentlab` table
- [ ] Container reaches `1.1.1.1`, cannot reach `192.168.1.1`
- [ ] Tailscale up, reachable from off-network
- [ ] Monitor can be switched off and the box keeps running

Then `HANDOFF.md` §6 — the day-0 credential checklist.
