#!/usr/bin/env bash
#
# setup_static_ip.sh — give this Pi a fixed IPv4 address on eth0 *in addition
# to* whatever DHCP hands it, so the SMORES-Topside API answers on a known
# address whether the Pi is plugged into a real network or straight into a
# laptop with no DHCP server on the wire.
#
# It edits the NetworkManager profile that owns the interface (adding a manual
# address alongside `ipv4.method=auto`), which persists across reboots.
#
# The subtlety, and the reason for the ipv6/dhcp-timeout settings below: a
# manual address is not enough on its own. NetworkManager tears down an
# activation — manual addresses included — if *every* address family fails,
# which is exactly what happens on a DHCP-less wire with no IPv6 router:
#
#   dhcp4 (eth0): state changed no lease
#   device (eth0): state change: ip-config -> failed (reason 'ip-config-unavailable')
#   avahi-daemon: Withdrawing address record for 192.168.1.55 on eth0.
#
# So this script also makes sure at least one family always succeeds
# (`ipv6.method=link-local`, which needs no router) and that IPv4 never
# reaches the failed state at all (`ipv4.dhcp-timeout=infinity`, so DHCP keeps
# retrying in the background instead of giving up).
#
# Usage:
#   sudo deploy/setup_static_ip.sh                 # apply the defaults
#   sudo deploy/setup_static_ip.sh --status        # show what is configured/live
#   sudo deploy/setup_static_ip.sh --self-test     # prove it survives no DHCP
#   sudo deploy/setup_static_ip.sh --remove        # undo it
#   sudo deploy/setup_static_ip.sh --address 192.168.1.55/24 --interface eth0
#
# See "A fixed IP for the Pi" in README.md for the why, and for alternatives.

set -euo pipefail

# ---------------------------------------------------------------- defaults --

IFACE="eth0"
STATIC_CIDR="192.168.1.55/24"

# "infinity" means NetworkManager keeps asking for a lease forever rather than
# declaring IPv4 failed. Two things follow: the static address is never torn
# down for want of a DHCP server, and moving the Pi from a bare cable onto a
# real network picks up a lease on its own, with no re-plug.
DHCP_TIMEOUT="infinity"

# Something has to succeed or NetworkManager fails the activation and removes
# every address. IPv6 link-local is the only method that can't fail: fe80::
# is derived locally, with no router, no server and no timeout.
IPV6_METHOD="link-local"

ACTION="apply"
ASSUME_YES="no"

PROG="${0##*/}"
ORIG_ARGS=("$@")

# ----------------------------------------------------------------- output --

note() { printf '\033[0;36m%s\033[0m\n' "$*"; }
ok()   { printf '\033[0;32m%s\033[0m\n' "$*"; }
warn() { printf '\033[0;33mwarning: %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[0;31merror: %s\033[0m\n' "$*" >&2; exit 1; }

usage() {
    cat <<EOF
$PROG — add a persistent static IPv4 address to an interface, keeping DHCP.

Options:
  -i, --interface NAME    Interface to configure       (default: $IFACE)
  -a, --address CIDR      Static address with prefix   (default: $STATIC_CIDR)
  -t, --dhcp-timeout SEC  Seconds before DHCP gives up (default: $DHCP_TIMEOUT)
                          'infinity' keeps retrying and never fails IPv4
      --ipv6 METHOD       ipv6.method to set           (default: $IPV6_METHOD)
                          one of: link-local, auto, ignore, disabled
  -s, --status            Show the configured and live addresses, then exit
      --self-test         Reproduce a DHCP-less link on a throwaway dummy
                          interface and check the settings survive it.
                          Does not touch $IFACE. Takes about 90 s.
  -r, --remove            Remove the static address again
  -y, --yes               Don't prompt before restarting the interface
  -h, --help              This message

Must be run as root, except for --status.
EOF
}

# ------------------------------------------------------------ arg parsing --

while [ $# -gt 0 ]; do
    case "$1" in
        -i|--interface)    IFACE="${2:?--interface needs a value}"; shift 2 ;;
        -a|--address)      STATIC_CIDR="${2:?--address needs a value}"; shift 2 ;;
        -t|--dhcp-timeout) DHCP_TIMEOUT="${2:?--dhcp-timeout needs a value}"; shift 2 ;;
        --ipv6)            IPV6_METHOD="${2:?--ipv6 needs a value}"; shift 2 ;;
        -s|--status)       ACTION="status"; shift ;;
        --self-test)       ACTION="selftest"; shift ;;
        -r|--remove)       ACTION="remove"; shift ;;
        -y|--yes)          ASSUME_YES="yes"; shift ;;
        -h|--help)         usage; exit 0 ;;
        *)                 usage >&2; die "unknown argument: $1" ;;
    esac
done

STATIC_IP="${STATIC_CIDR%%/*}"
STATIC_PREFIX="${STATIC_CIDR#*/}"

# --------------------------------------------------------- IPv4 utilities --

# Dotted quad -> 32-bit integer. Returns non-zero if it isn't a valid address.
ip_to_int() {
    local ip="$1" a b c d IFS=.
    read -r a b c d <<<"$ip"
    for o in "$a" "$b" "$c" "$d"; do
        [[ "$o" =~ ^[0-9]{1,3}$ ]] || return 1
        [ "$o" -le 255 ] || return 1
    done
    printf '%s' "$(( (a << 24) | (b << 16) | (c << 8) | d ))"
}

# Network address of ip/prefix, as an integer.
network_of() {
    local int="$1" prefix="$2" mask
    mask=$(( (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF ))
    printf '%s' "$(( int & mask ))"
}

validate_args() {
    [[ "$STATIC_CIDR" == */* ]] || die "--address must include a prefix, e.g. 192.168.1.55/24"
    [[ "$STATIC_PREFIX" =~ ^[0-9]{1,2}$ ]] && [ "$STATIC_PREFIX" -ge 1 ] && [ "$STATIC_PREFIX" -le 32 ] \
        || die "bad prefix length in '$STATIC_CIDR'"
    ip_to_int "$STATIC_IP" >/dev/null || die "bad IPv4 address in '$STATIC_CIDR'"

    case "$DHCP_TIMEOUT" in
        infinity) ;;
        ''|*[!0-9]*) die "--dhcp-timeout must be a whole number of seconds, or 'infinity'" ;;
    esac

    case "$IPV6_METHOD" in
        link-local) ;;
        auto|ignore|disabled)
            warn "--ipv6 $IPV6_METHOD leaves no address family that is guaranteed to succeed."
            warn "On a wire with no DHCP server NetworkManager may fail the activation and"
            warn "drop $STATIC_IP with it. 'link-local' is the setting that prevents that." ;;
        *)  die "--ipv6 must be one of: link-local, auto, ignore, disabled" ;;
    esac
}

# ------------------------------------------------------- preflight checks --

require_root() {
    [ "$(id -u)" -eq 0 ] || die "must run as root — try: sudo $0${ORIG_ARGS[*]:+ ${ORIG_ARGS[*]}}"
}

require_nm_running() {
    command -v nmcli >/dev/null 2>&1 || die "nmcli not found; this script only handles NetworkManager"
    if ! systemctl is-active --quiet NetworkManager; then
        printf '%s\n' \
            "NetworkManager is not running, so this script can't configure anything." \
            "This Pi is presumably on systemd-networkd or dhcpcd instead; see the" \
            "'Not running NetworkManager?' note in README.md for the equivalent" \
            "config for those stacks." >&2
        exit 1
    fi
}

require_nm() {
    require_nm_running
    nmcli -t -f DEVICE device status | grep -Fqx "$IFACE" \
        || die "NetworkManager doesn't know an interface called '$IFACE' (try: nmcli device status)"
}

# --------------------------------------------------------- profile lookup --

# The keyfile backing a profile. /run/... means volatile (NM's auto-generated
# "Wired connection 1"); /etc/... means it survives a reboot.
profile_filename() {
    local uuid="$1" line
    while IFS= read -r line; do
        [ "${line%%:*}" = "$uuid" ] || continue
        printf '%s' "${line#*:}"
        return 0
    done < <(nmcli -g UUID,FILENAME connection show)
    return 1
}

# Pick the profile to edit: whatever is active on the interface, else a saved
# profile bound to it, else nothing (caller creates one).
find_profile() {
    local uuid ifname
    uuid="$(nmcli -g GENERAL.CON-UUID device show "$IFACE" 2>/dev/null || true)"
    if [ -n "$uuid" ] && [ "$uuid" != "--" ]; then
        printf '%s' "$uuid"
        return 0
    fi
    while IFS= read -r uuid; do
        [ -n "$uuid" ] || continue
        ifname="$(nmcli -g connection.interface-name connection show "$uuid" 2>/dev/null || true)"
        [ "$ifname" = "$IFACE" ] || continue
        printf '%s' "$uuid"
        return 0
    done < <(nmcli -g UUID connection show)
    return 1
}

profile_name() { nmcli -g connection.id connection show "$1"; }

# ----------------------------------------------------------------- status --

addresses_on() { ip -4 -oneline addr show dev "$1" | awk '{print $4}'; }

live_addresses() { addresses_on "$IFACE"; }

has_live_address() { live_addresses | grep -Fqx "$STATIC_CIDR"; }

device_state() { nmcli -g GENERAL.STATE device show "$1" 2>/dev/null || printf 'absent'; }

show_status() {
    local uuid configured
    if ! uuid="$(find_profile)"; then
        warn "no NetworkManager profile is associated with $IFACE"
    else
        note "Profile:   $(profile_name "$uuid")  [$uuid]"
        note "Keyfile:   $(profile_filename "$uuid")"
        configured="$(nmcli -g ipv4.addresses connection show "$uuid")"
        note "ipv4.method:        $(nmcli -g ipv4.method connection show "$uuid")"
        note "ipv4.addresses:     ${configured:-<none>}"
        note "ipv4.dhcp-timeout:  $(nmcli -g ipv4.dhcp-timeout connection show "$uuid")"
        note "ipv6.method:        $(nmcli -g ipv6.method connection show "$uuid")"
        note "autoconnect-retries: $(nmcli -g connection.autoconnect-retries connection show "$uuid")"
    fi
    note "Device state: $(device_state "$IFACE")"
    note "Live on $IFACE:"
    live_addresses | sed 's/^/  /'
    if has_live_address; then
        ok "$STATIC_CIDR is up on $IFACE"
        return 0
    fi
    warn "$STATIC_CIDR is NOT currently on $IFACE"
    return 1
}

# ------------------------------------------------------------- sanity: IP --

# A static address inside the DHCP subnet gives the interface two addresses on
# one network, with two identical on-link routes. It mostly works but it is
# never what you want, so say so loudly.
warn_on_subnet_overlap() {
    local static_int dhcp_cidr dhcp_ip dhcp_prefix dhcp_int prefix
    static_int="$(ip_to_int "$STATIC_IP")"
    while IFS= read -r dhcp_cidr; do
        [ -n "$dhcp_cidr" ] || continue
        [ "$dhcp_cidr" = "$STATIC_CIDR" ] && continue
        dhcp_ip="${dhcp_cidr%%/*}"
        dhcp_prefix="${dhcp_cidr#*/}"
        dhcp_int="$(ip_to_int "$dhcp_ip")" || continue
        prefix=$(( STATIC_PREFIX < dhcp_prefix ? STATIC_PREFIX : dhcp_prefix ))
        if [ "$(network_of "$static_int" "$prefix")" = "$(network_of "$dhcp_int" "$prefix")" ]; then
            warn "$STATIC_CIDR overlaps the address $IFACE already has ($dhcp_cidr)."
            warn "Pick a static address on a subnet this network doesn't use."
        fi
    done < <(live_addresses)
}

# Best effort duplicate-address probe. Silent no-op when arping is missing or
# the link is down; a positive result is worth stopping for.
#
# iputils arping -D exits 0 when nobody answers (the address is free) and 1
# when someone does. Any other status is arping itself failing, which is not
# evidence of a conflict — stay quiet rather than cry wolf.
warn_on_address_in_use() {
    local rc=0
    command -v arping >/dev/null 2>&1 || return 0
    ip link show dev "$IFACE" | grep -q 'LOWER_UP' || return 0
    arping -q -D -c 2 -w 3 -I "$IFACE" "$STATIC_IP" >/dev/null 2>&1 || rc=$?
    [ "$rc" -eq 1 ] || return 0
    warn "another host on $IFACE already answers for $STATIC_IP."
    warn "Two machines with the same address will break both. Choose a different one."
}

# ------------------------------------------------------------------ apply --

confirm_bounce() {
    [ "$ASSUME_YES" = "yes" ] && return 0
    [ -t 0 ] || return 0
    local reply
    printf '%s' "Restart $IFACE now? Any SSH session over it will drop. [y/N] "
    read -r reply
    case "$reply" in [yY]*) return 0 ;; *) return 1 ;; esac
}

# Push the saved profile onto the running interface. `device reapply` merges
# the change without taking the link down, which keeps remote sessions alive;
# only if that is refused do we fall back to a full re-activation.
apply_to_running_interface() {
    local uuid="$1"
    if nmcli device reapply "$IFACE" >/dev/null 2>&1; then
        note "Reapplied the profile to $IFACE without dropping the link."
        return 0
    fi
    warn "NetworkManager could not reapply in place; the interface must be restarted."
    if ! confirm_bounce; then
        warn "Skipped. The change is saved and will take effect on the next boot,"
        warn "or run: nmcli connection up '$(profile_name "$uuid")'"
        return 1
    fi
    nmcli connection up "$uuid" >/dev/null
    note "Restarted $IFACE."
}

wait_for_address() {
    local want_present="$1" i
    for i in $(seq 1 20); do
        if has_live_address; then
            [ "$want_present" = "yes" ] && return 0
        else
            [ "$want_present" = "no" ] && return 0
        fi
        sleep 0.5
    done
    return 1
}

do_apply() {
    warn_on_subnet_overlap
    warn_on_address_in_use

    local uuid name existing filename
    if uuid="$(find_profile)"; then
        name="$(profile_name "$uuid")"
        note "Using existing NetworkManager profile '$name' for $IFACE."
    else
        name="smores-$IFACE"
        note "No profile found for $IFACE; creating '$name'."
        nmcli connection add type ethernet ifname "$IFACE" con-name "$name" \
            connection.autoconnect yes ipv4.method auto >/dev/null
        uuid="$(nmcli -g connection.uuid connection show "$name")"
    fi

    # `ipv4.method auto` keeps DHCP; addresses listed in `ipv4.addresses` are
    # configured on top of the lease rather than instead of it. No gateway is
    # set for the static address on purpose — the default route stays with
    # DHCP when there is one.
    existing="$(nmcli -g ipv4.addresses connection show "$uuid")"
    if printf '%s' "$existing" | tr ',' '\n' | sed 's/^ *//' | grep -Fqx "$STATIC_CIDR"; then
        note "$STATIC_CIDR is already in the profile."
    else
        nmcli connection modify "$uuid" +ipv4.addresses "$STATIC_CIDR"
        note "Added $STATIC_CIDR to the profile."
    fi

    # See the header comment: the three settings after ipv4.addresses are what
    # stop a missing DHCP server from taking the static address down with it.
    # autoconnect-retries=0 is the last line of defence — if activation ever
    # does fail, keep retrying forever rather than giving up after 4 tries and
    # leaving the Pi dark until somebody re-seats the cable.
    nmcli connection modify "$uuid" \
        ipv4.method auto \
        ipv4.may-fail yes \
        ipv4.dhcp-timeout "$DHCP_TIMEOUT" \
        ipv6.method "$IPV6_METHOD" \
        ipv6.may-fail yes \
        connection.autoconnect yes \
        connection.autoconnect-retries 0
    note "Set ipv4.dhcp-timeout=$DHCP_TIMEOUT, ipv6.method=$IPV6_METHOD, autoconnect-retries=forever."

    # Editing NM's auto-generated "Wired connection 1" normally promotes it from
    # the volatile /run store into /etc, which is what makes this persistent.
    # If some NM version doesn't, say so rather than silently losing it at boot.
    filename="$(profile_filename "$uuid")"
    case "$filename" in
        /etc/*) note "Saved to $filename (persists across reboots)." ;;
        *)      warn "profile is stored at $filename, outside /etc — it will NOT survive a reboot."
                warn "Make a persistent copy with:"
                warn "  nmcli connection clone '$name' smores-$IFACE"
                warn "  nmcli connection up smores-$IFACE" ;;
    esac

    apply_to_running_interface "$uuid" || exit 1

    if wait_for_address yes; then
        ok "$STATIC_IP is live on $IFACE."
    else
        warn "$STATIC_CIDR did not appear on $IFACE within 10 s."
        warn "Check: nmcli device show $IFACE  /  journalctl -u NetworkManager -n 50"
    fi
    printf '\n'
    show_status
}

# ----------------------------------------------------------------- remove --

do_remove() {
    local uuid name
    uuid="$(find_profile)" || die "no NetworkManager profile is associated with $IFACE"
    name="$(profile_name "$uuid")"

    nmcli connection modify "$uuid" -ipv4.addresses "$STATIC_CIDR"
    nmcli connection modify "$uuid" \
        ipv4.dhcp-timeout 0 \
        ipv6.method auto \
        connection.autoconnect-retries -1
    note "Removed $STATIC_CIDR from '$name' and restored NetworkManager's defaults"
    note "for ipv4.dhcp-timeout, ipv6.method and connection.autoconnect-retries."

    apply_to_running_interface "$uuid" || exit 1
    if wait_for_address no; then
        ok "$STATIC_IP is no longer on $IFACE."
    else
        warn "$STATIC_CIDR is still on $IFACE; a reboot will clear it."
    fi
}

# -------------------------------------------------------------- self-test --
#
# A dummy interface is a faithful stand-in for a cable with nothing on the far
# end: NetworkManager runs the same IP configuration over it, DHCP requests go
# nowhere, and no router advertisements ever arrive. So we can reproduce the
# exact failure on a throwaway device instead of unplugging the Pi.

PROBE_IFACE="smoresprobe0"
PROBE_CIDR="192.168.234.55/24"
PROBE_PROFILE="smores-selftest"

probe_cleanup() {
    nmcli connection delete "$PROBE_PROFILE" >/dev/null 2>&1 || true
    ip link delete "$PROBE_IFACE" >/dev/null 2>&1 || true
}

# Bring up a dummy device with the given settings and watch it for a while.
# Prints a one-line verdict; returns 0 if the address was still there at the
# end, 1 if it was never there or got taken away.
probe_case() {
    local label="$1" dhcp_timeout="$2" ipv6_method="$3" ra_timeout="$4" watch="$5"
    local i state seen_address=no lost_at="" final_state

    probe_cleanup
    nmcli connection add type dummy ifname "$PROBE_IFACE" con-name "$PROBE_PROFILE" \
        connection.autoconnect no \
        ipv4.method auto ipv4.addresses "$PROBE_CIDR" ipv4.may-fail yes \
        ipv4.dhcp-timeout "$dhcp_timeout" \
        ipv6.method "$ipv6_method" ipv6.may-fail yes ipv6.ra-timeout "$ra_timeout" \
        >/dev/null

    note "  $label"
    note "    ipv4.dhcp-timeout=$dhcp_timeout ipv6.method=$ipv6_method — watching ${watch}s"
    # Activation legitimately fails in the case we are trying to reproduce.
    nmcli connection up "$PROBE_PROFILE" >/dev/null 2>&1 || true

    for i in $(seq 1 "$watch"); do
        if addresses_on "$PROBE_IFACE" 2>/dev/null | grep -Fqx "$PROBE_CIDR"; then
            seen_address=yes
        elif [ "$seen_address" = yes ] && [ -z "$lost_at" ]; then
            lost_at="$i"
        fi
        sleep 1
    done

    state="$(device_state "$PROBE_IFACE")"
    final_state="${state:-absent}"
    if addresses_on "$PROBE_IFACE" 2>/dev/null | grep -Fqx "$PROBE_CIDR"; then
        ok "    held $PROBE_CIDR for ${watch}s; device state: $final_state"
        probe_cleanup
        return 0
    fi
    if [ -n "$lost_at" ]; then
        warn "   lost $PROBE_CIDR after ${lost_at}s; device state: $final_state"
    else
        warn "   never got $PROBE_CIDR; device state: $final_state"
    fi
    probe_cleanup
    return 1
}

do_selftest() {
    modprobe dummy 2>/dev/null || true
    trap probe_cleanup EXIT INT TERM

    note "Self-test: simulating a cable with no DHCP server, on a dummy"
    note "interface ($PROBE_IFACE). $IFACE is not touched."
    printf '\n'

    local broken_held=0 fixed_held=0

    note "1/2 — settings WITHOUT the fix (short DHCP timeout, ipv6.method=auto):"
    probe_case "expected to fail" 10 auto 10 25 && broken_held=1
    printf '\n'

    note "2/2 — settings this script applies (ipv6.method=$IPV6_METHOD,"
    note "      ipv4.dhcp-timeout=$DHCP_TIMEOUT):"
    probe_case "expected to hold" "$DHCP_TIMEOUT" "$IPV6_METHOD" 10 50 && fixed_held=1
    printf '\n'

    trap - EXIT INT TERM
    probe_cleanup

    if [ "$broken_held" -eq 1 ]; then
        warn "Case 1 did not reproduce the failure, so a dummy interface isn't a"
        warn "faithful model of your hardware here and case 2 proves little."
        warn "Test on the real cable instead: plug into the laptop, wait a minute,"
        warn "then run '$PROG --status'."
        return 1
    fi
    if [ "$fixed_held" -eq 1 ]; then
        ok "Confirmed: the old settings drop the static address on a DHCP-less"
        ok "link and the settings this script applies keep it."
        return 0
    fi
    warn "The settings this script applies did NOT hold the address either."
    warn "Send the output above plus 'journalctl -u NetworkManager -n 100'."
    return 1
}

# ------------------------------------------------------------------- main --

validate_args

case "$ACTION" in
    status)   require_nm; show_status ;;
    apply)    require_root; require_nm; do_apply ;;
    remove)   require_root; require_nm; do_remove ;;
    selftest) require_root; require_nm_running; do_selftest ;;
esac
