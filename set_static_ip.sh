#!/bin/bash

# Usage: ./set_static_ip.sh <new_ip>
# Example: ./set_static_ip.sh 192.168.1.100

# Validate input
if [ -z "$1" ]; then
  echo "Usage: $0 <new_ip>"
  exit 1
fi

NEW_IP=$1
INTERFACE="eth0"
NETMASK="255.255.255.0" # Adjust as needed
GATEWAY="192.168.1.1"  # Replace with your actual gateway
DNS="8.8.8.8"

# Backup the interfaces file
BACKUP_PATH="/etc/network/interfaces.bak_$(date +%Y%m%d%H%M%S)"
echo "Creating backup of /etc/network/interfaces at $BACKUP_PATH"
sudo cp /etc/network/interfaces "$BACKUP_PATH"

# Update /etc/network/interfaces
echo "Updating /etc/network/interfaces with static IP: $NEW_IP"
sudo bash -c "cat > /etc/network/interfaces" <<EOF
auto $INTERFACE
iface $INTERFACE inet static
    address $NEW_IP
    netmask $NETMASK
    gateway $GATEWAY
    dns-nameservers $DNS
EOF

# Flush existing IP configurations
echo "Flushing existing IP configurations for $INTERFACE"
sudo ip addr flush dev $INTERFACE

# Release DHCP leases (if any)
echo "Releasing DHCP leases for $INTERFACE"
sudo dhclient -r $INTERFACE

# Restart networking
echo "Restarting networking service..."
sudo ifdown $INTERFACE
sudo ifup $INTERFACE

# Verify the changes
echo "Verifying the new IP address configuration..."
NEW_ASSIGNED_IP=$(ip addr show $INTERFACE | grep "inet " | awk '{print $2}' | cut -d/ -f1)
if [ "$NEW_ASSIGNED_IP" == "$NEW_IP" ]; then
  echo "Successfully updated IP to $NEW_ASSIGNED_IP"
else
  echo "Failed to update IP. Current IP: $NEW_ASSIGNED_IP"
  echo "Check /etc/network/interfaces for errors."
fi
