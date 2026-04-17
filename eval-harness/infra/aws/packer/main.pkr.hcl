packer {
  required_plugins {
    amazon = {
      version = ">= 1.3.0"
      source  = "github.com/hashicorp/amazon"
    }
  }
}

source "amazon-ebs" "golden" {
  region        = var.aws_region
  instance_type = var.instance_type
  communicator  = "ssh"
  ssh_username  = var.ssh_username
  ssh_timeout   = "10m"

  source_ami_filter {
    filters = {
      name                = var.source_ami_filter_name
      root-device-type    = "ebs"
      virtualization-type = "hvm"
      architecture        = "x86_64"
    }
    most_recent = true
    owners      = [var.source_ami_owner]
  }

  ami_name        = "${var.ami_name_prefix}-{{timestamp}}"
  ami_description = "Eval harness golden image for ${var.target_image_alias}"

  tags = {
    Name            = var.target_image_alias
    EvalHarness     = "true"
    EvalImageRole   = "golden"
    EvalTargetImage = var.target_image_alias
    DistroFamily    = var.distro_family
    ManagedBy       = "eval-harness"
  }

  dynamic "vpc_filter" {
    for_each = var.vpc_id != "" ? [] : [1]
    content {
      filters = {
        "isDefault" = "true"
      }
    }
  }

  vpc_id                      = var.vpc_id != "" ? var.vpc_id : null
  subnet_id                   = var.subnet_id != "" ? var.subnet_id : null
  associate_public_ip_address = true
  iam_instance_profile        = var.iam_instance_profile

  launch_block_device_mappings {
    device_name           = "/dev/xvda"
    volume_size           = 20
    volume_type           = "gp3"
    delete_on_termination = true
  }
}

build {
  sources = ["source.amazon-ebs.golden"]

  provisioner "shell" {
    environment_vars = [
      "DISTRO_FAMILY=${var.distro_family}",
    ]
    scripts = [
      "scripts/00-base-packages.sh",
      "scripts/03-ssm-agent.sh",
      "scripts/04-eval-user.sh",
      "scripts/05-cleanup.sh",
    ]
    execute_command = "chmod +x '{{ .Path }}'; sudo env {{ .Vars }} bash '{{ .Path }}'"
  }

  post-processor "manifest" {
    output     = var.manifest_output
    strip_path = true
  }
}
