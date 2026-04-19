from enum import Enum


class SourceFamily(str, Enum):
    debian = "debian"
    ubuntu = "ubuntu"
    arch = "arch"
    fedora = "fedora"
    rhel = "rhel"
    alpine = "alpine"
    nixos = "nixos"
    gentoo = "gentoo"
    proxmox = "proxmox"
    kernel = "kernel"
    systemd = "systemd"
    docker = "docker"
    kubernetes = "kubernetes"
    btrfs = "btrfs"
    zfs = "zfs"
    ceph = "ceph"
    postgres = "postgres"
    mysql = "mysql"
    sqlite = "sqlite"
    redis = "redis"
    nginx = "nginx"
    apache = "apache"
    openssh = "openssh"
    network_manager = "network_manager"
    grub = "grub"
    lvm = "lvm"
    linux_generic = "linux_generic"
    bsd_generic = "bsd_generic"
    other_unix = "other_unix"
    other = "other"


class VendorOrProject(str, Enum):
    debian_project = "debian_project"
    ubuntu_canonical = "ubuntu_canonical"
    proxmox_server_solutions = "proxmox_server_solutions"
    docker_inc = "docker_inc"
    linux_foundation = "linux_foundation"
    apache_foundation = "apache_foundation"
    postgres_global_dev_group = "postgres_global_dev_group"
    nginx_inc = "nginx_inc"
    red_hat = "red_hat"
    suse = "suse"
    arch_linux = "arch_linux"
    fedora_project = "fedora_project"
    alpine_linux = "alpine_linux"
    community = "community"
    unknown = "unknown"


class DocKind(str, Enum):
    admin_guide = "admin_guide"
    reference = "reference"
    install_guide = "install_guide"
    tutorial = "tutorial"
    manpage = "manpage"
    wiki = "wiki"
    release_notes = "release_notes"
    faq = "faq"
    api_docs = "api_docs"
    book = "book"
    whitepaper = "whitepaper"
    changelog = "changelog"
    troubleshooting = "troubleshooting"
    spec = "spec"
    other = "other"


class TrustTier(str, Enum):
    canonical = "canonical"
    official = "official"
    community = "community"
    unofficial = "unofficial"
    unknown = "unknown"


class FreshnessStatus(str, Enum):
    current = "current"
    supported = "supported"
    legacy = "legacy"
    deprecated = "deprecated"
    archived = "archived"
    unknown = "unknown"


class OsFamily(str, Enum):
    linux = "linux"
    bsd = "bsd"
    windows = "windows"
    macos = "macos"
    unix = "unix"
    any = "any"
    proprietary = "proprietary"
    unknown = "unknown"


class InitSystem(str, Enum):
    systemd = "systemd"
    openrc = "openrc"
    sysv = "sysv"
    runit = "runit"
    launchd = "launchd"
    bsd_init = "bsd_init"
    none = "none"
    unknown = "unknown"


class PackageManager(str, Enum):
    apt = "apt"
    dpkg = "dpkg"
    rpm = "rpm"
    yum = "yum"
    dnf = "dnf"
    pacman = "pacman"
    portage = "portage"
    apk = "apk"
    nix = "nix"
    zypper = "zypper"
    brew = "brew"
    choco = "choco"
    scoop = "scoop"
    pip = "pip"
    none = "none"
    unknown = "unknown"


class MajorSubsystem(str, Enum):
    networking = "networking"
    storage = "storage"
    virtualization = "virtualization"
    containers = "containers"
    security = "security"
    kernel = "kernel"
    boot = "boot"
    init = "init"
    filesystems = "filesystems"
    clustering = "clustering"
    backup = "backup"
    gui = "gui"
    cli = "cli"
    observability = "observability"
    package_management = "package_management"
    user_management = "user_management"
    scheduling = "scheduling"
    databases = "databases"
    web = "web"
    dns = "dns"
    mail = "mail"
    auth = "auth"
    crypto = "crypto"
    logging = "logging"
    hardware = "hardware"
    drivers = "drivers"


class ChunkType(str, Enum):
    narrative = "narrative"
    list_item = "list_item"
    code = "code"
    table = "table"
    heading = "heading"
    caption = "caption"
    uncategorized = "uncategorized"


class IngestSourceType(str, Enum):
    pdf_operator = "pdf_operator"
    pdf_crawl = "pdf_crawl"
    html = "html"
    manpage = "manpage"
    markdown = "markdown"
    other = "other"


ALL_ENUMS: dict[str, type[Enum]] = {
    "source_family": SourceFamily,
    "vendor_or_project": VendorOrProject,
    "doc_kind": DocKind,
    "trust_tier": TrustTier,
    "freshness_status": FreshnessStatus,
    "os_family": OsFamily,
    "init_systems": InitSystem,
    "package_managers": PackageManager,
    "major_subsystems": MajorSubsystem,
    "chunk_type": ChunkType,
    "ingest_source_type": IngestSourceType,
}


def _normalize(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def coerce_enum(field_name: str, value: str | None) -> str:
    enum_cls = ALL_ENUMS[field_name]  # raises KeyError if unknown field
    if not value:
        return "unknown"
    normalized = _normalize(value)
    try:
        return enum_cls(normalized).value
    except ValueError:
        return "unknown"


def coerce_enum_list(field_name: str, values: list[str | None] | None) -> list[str]:
    if not values:
        return ["unknown"]
    coerced = [coerce_enum(field_name, v) for v in values]
    non_unknown = sorted(set(v for v in coerced if v != "unknown"))
    return non_unknown if non_unknown else ["unknown"]
