
# Apps
- kured
- piraeus
- system-upgrade
- linstor-affinity
- cert-manager + Cloudflare DNS-01 issuers (`letsencrypt-dns`, `letsencrypt-dns-staging`)
- wildcard `*.lenatin.org` certificate + Traefik default TLSStore
- CoreDNS override resolving `mqtt.lenatin.org` to the Mosquitto LAN VIP (the
  broker cert's only SAN has no public record)
