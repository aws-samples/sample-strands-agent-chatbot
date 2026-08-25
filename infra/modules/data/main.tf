resource "aws_dynamodb_table" "users" {
  name         = "${var.project_name}-users-v2"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "userId"
  range_key    = "sk"

  attribute {
    name = "userId"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    enabled        = true
    attribute_name = "ttl"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Component = "data"
  }
}

resource "aws_dynamodb_table" "sessions" {
  name         = "${var.project_name}-sessions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "sessionId"
  range_key    = "userId"

  attribute {
    name = "sessionId"
    type = "S"
  }

  attribute {
    name = "userId"
    type = "S"
  }

  attribute {
    name = "lastMessageAt"
    type = "S"
  }

  global_secondary_index {
    name            = "UserSessionsIndex"
    projection_type = "ALL"

    key_schema {
      attribute_name = "userId"
      key_type       = "HASH"
    }
    key_schema {
      attribute_name = "lastMessageAt"
      key_type       = "RANGE"
    }
  }

  ttl {
    enabled        = true
    attribute_name = "ttl"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Component = "data"
  }
}

resource "aws_dynamodb_table" "session_orchestration" {
  name         = "${var.project_name}-session-orchestration"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "sessionKey"
  range_key    = "recordKey"

  attribute {
    name = "sessionKey"
    type = "S"
  }

  attribute {
    name = "recordKey"
    type = "S"
  }

  attribute {
    name = "workStatus"
    type = "S"
  }

  attribute {
    name = "heartbeatAt"
    type = "S"
  }

  global_secondary_index {
    name            = "DelegationWorkIndex"
    hash_key        = "workStatus"
    range_key       = "heartbeatAt"
    projection_type = "INCLUDE"
    non_key_attributes = [
      "recordType",
      "desiredState",
      "userId",
      "sessionId",
      "jobId",
    ]
  }

  ttl {
    enabled        = true
    attribute_name = "ttl"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  stream_enabled   = true
  stream_view_type = "NEW_IMAGE"

  tags = {
    Component = "session-orchestration"
  }
}

resource "aws_dynamodb_table" "session_files" {
  name         = "${var.project_name}-session-files"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "sessionKey"
  range_key    = "recordKey"

  attribute {
    name = "sessionKey"
    type = "S"
  }

  attribute {
    name = "recordKey"
    type = "S"
  }

  ttl {
    enabled        = true
    attribute_name = "ttl"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Component = "session-files"
  }
}
