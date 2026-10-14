import keyring
from netmiko import ConnectHandler

#=========================
# KEYRING SETTINGS
#=========================

service_name = ""
username = ""
password = keyring.get_password(service_name, username)

if not password:
    raise ValueError(
        f"Password for {username} not found in keyring "
        f"under service name '{service_name}'"
    )

# =========================
# SWITCH SETTINGS
# =========================

SWITCH = {
    "device_type": "cisco_ios",
    "host": "",
    "username": username,
    "password": password,
}

DRY_RUN = True
WEEKS_THRESHOLD = 26

IGNORE_PREFIXES = ("OR-", "NS-", "PX-", "SL-")


# =========================
# INTERFACE HELPERS
# =========================

def normalize_interface(interface):
    """Convert full interface names to Cisco abbreviated names."""
    if interface.lower().startswith("gigabitethernet"):
        return "Gi" + interface[len("GigabitEthernet"):]

    return interface


def is_gigabit_port(interface):
    """Accept only GiX/0/X ports."""
    interface = normalize_interface(interface)

    if not interface.startswith("Gi"):
        return False

    try:
        slot, stack, port = map(
            int,
            interface[2:].split("/")
        )

        return stack == 0

    except (ValueError, IndexError):
        return False

# =========================
# FIND EXCLUDED PORTS
# =========================

def get_excluded_ports(connection):
    output = connection.send_command(
        "show interfaces description",
        read_timeout=120,
    )

    excluded_ports = set()

    for line in output.splitlines():
        parts = line.split(None, 3)

        if not parts:
            continue

        interface = normalize_interface(parts[0])

        if not is_gigabit_port(interface):
            continue

        description = (
            parts[3].strip().upper()
            if len(parts) > 3
            else ""
        )

        # Only exclude protected descriptions
        if description.startswith(IGNORE_PREFIXES):
            excluded_ports.add(interface)
            print(
                f"EXCLUDED (DESCRIPTION): "
                f"{interface} - {description}"
            )

    return excluded_ports
# =========================
# FIND INACTIVE PORTS
# =========================


def get_inactive_ports(connection, excluded_ports):
    output = connection.send_command("show interfaces", read_timeout=120)
    inactive_ports = {}
    current_int = None

    for line in output.splitlines():
        stripped = line.strip()

        # Detect actual interface header lines only
        if " is " in stripped:
            first_part = stripped.split(" is ", 1)[0].strip()

            if (
                first_part.startswith("GigabitEthernet")
                or first_part.startswith("Gi")
            ):
                interface = normalize_interface(first_part)

                if (
                    is_gigabit_port(interface)
                    and "(notconnect)" in stripped.lower()
                ):
                    current_int = interface
                else:
                    current_int = None

                continue

        if current_int is None:
            continue

        # Never disable ports with protected descriptions
        if current_int in excluded_ports:
            continue

        if "Last input" not in stripped:
            continue

        last_input = stripped.split("Last input", 1)[1]
        last_input = last_input.strip().split(",", 1)[0].strip()

        # Include ports that have never been used
        if last_input.lower() == "never":
            inactive_ports[current_int] = "NEVER USED"
            print(f"FOUND NEVER-USED PORT: {current_int}")
            continue

        # Include ports inactive for 16 weeks or longer
        if "w" in last_input:
            weeks_text = last_input.split("w", 1)[0].strip()

            if weeks_text.isdigit():
                weeks = int(weeks_text)

                if weeks >= WEEKS_THRESHOLD:
                    inactive_ports[current_int] = (
                        f"{weeks} weeks inactive"
                    )

    return inactive_ports

# =========================
# MAIN
# =========================

connection = None

try:
    print("Connecting to switch...")
    connection = ConnectHandler(**SWITCH)

    print("\nChecking interface descriptions...")
    excluded_ports = get_excluded_ports(connection)

    print(f"\nTotal excluded ports: {len(excluded_ports)}")

    print("\nChecking last input times...")
    inactive_ports = get_inactive_ports(
        connection,
        excluded_ports,
    )

    print("\n========== PORTS TO DISABLE ==========")

    if not inactive_ports:
        print("No eligible inactive ports found.")

    for port, reason in sorted(inactive_ports.items()):
        print(f"{port} - {reason}")

    print(f"\nTotal eligible ports: {len(inactive_ports)}")

    if DRY_RUN:
        print("\nDRY RUN: No changes will be made.")

        for port in sorted(inactive_ports):
            print(f"Would disable {port}")

    elif inactive_ports:
        print("\nApplying configuration changes...")

        for port in sorted(inactive_ports):
            print(f"Disabling {port}")

            connection.send_config_set([
                f"interface {port}",
                "shutdown",
            ])

        connection.save_config()
        print("\nConfiguration saved.")

    else:
        print("\nNo ports to disable. Configuration unchanged.")

finally:
    if connection:
        connection.disconnect()
        print("\nDisconnected from switch.")
