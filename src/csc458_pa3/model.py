"""Fixed experiment contract; importing it never configures the network."""
REVISION = "3.0-home-download"
SCHEMA_VERSION = 3
EXPERIMENT = "home-download"
SERVER_IP = "10.0.1.1"
HOME_IP = "10.0.2.2"
SERVER_PORT = 5201
NOMINAL_IP_PACKET_BYTES = 1000
TCP_MSS_BYTES = 960
BASE_PROPAGATION_RTT_MS = 50.0
EXPECTED_INTERFACES = {
    "s": {"s-data"},
    "r": {"r-left", "r-right"},
    "d": {"d-data"},
}
EXPECTED_LINKS = {
    frozenset(("s-data", "r-left")),
    frozenset(("r-right", "d-data")),
}
# (namespace, outgoing interface, rate Mbit/s, delay ms, fixed limit or None)
# None identifies the ONE variable limit used by the buffer-size experiment.
QDISC_PROFILE = (
    ("s", "s-data", 50.0, 20.0, 10000),
    ("r", "r-left", 50.0, 20.0, 10000),
    ("r", "r-right", 10.0, 5.0, None),
    ("d", "d-data", 10.0, 5.0, 10000),
)
