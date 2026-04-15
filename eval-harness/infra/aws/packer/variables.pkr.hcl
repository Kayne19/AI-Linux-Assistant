variable "aws_region" {
  type    = string
  default = "us-west-2"
}

variable "vpc_id" {
  type    = string
  default = ""
}

variable "subnet_id" {
  type        = string
  description = "Leave empty to auto-select a public subnet."
}

variable "instance_type" {
  type    = string
  default = "t3.small"
}

variable "iam_instance_profile" {
  type        = string
  default     = "EvalSSMInstanceProfile"
  description = "Instance profile with AmazonSSMManagedInstanceCore."
}

variable "source_ami_filter_name" {
  type = string
}

variable "source_ami_owner" {
  type = string
}

variable "ssh_username" {
  type = string
}

variable "distro_family" {
  type = string
  validation {
    condition     = contains(["debian", "rhel"], var.distro_family)
    error_message = "Distro family must be either 'debian' or 'rhel'."
  }
}

variable "ami_name_prefix" {
  type = string
}

variable "target_image_alias" {
  type = string
}

variable "openclaw_version" {
  type    = string
  default = "2026.4.11"
}

variable "openclaw_eval_token" {
  type      = string
  sensitive = true
}

variable "node_major_version" {
  type    = string
  default = "24"
}

variable "manifest_output" {
  type    = string
  default = "packer-manifest.json"
}
