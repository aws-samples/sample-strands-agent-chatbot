#!/bin/bash

echo "🔍 Setting up AgentCore Observability..."

# Check AWS CLI and credentials
if ! command -v aws &> /dev/null; then
    echo "❌ AWS CLI not found. Please install AWS CLI first."
    exit 1
fi

if ! aws sts get-caller-identity &> /dev/null; then
    echo "❌ AWS credentials not configured. Please run 'aws configure' first."
    exit 1
fi

# Get AWS region
AWS_REGION=${AWS_REGION:-$(aws configure get region)}
if [ -z "$AWS_REGION" ]; then
    echo "❌ AWS region not set. Please set AWS_REGION environment variable or configure default region."
    exit 1
fi

echo "✅ Using AWS region: $AWS_REGION"

# Create CloudWatch Log Group for AgentCore
LOG_GROUP_NAME="agents/strands-agent-logs"
echo "📋 Creating CloudWatch Log Group: $LOG_GROUP_NAME"

if aws logs describe-log-groups --log-group-name-prefix "$LOG_GROUP_NAME" --region "$AWS_REGION" | grep -q "$LOG_GROUP_NAME"; then
    echo "✅ Log group '$LOG_GROUP_NAME' already exists"
else
    aws logs create-log-group --log-group-name "$LOG_GROUP_NAME" --region "$AWS_REGION"
    echo "✅ Created log group: $LOG_GROUP_NAME"
fi

# Log Stream will be auto-created by ADOT
TIMESTAMP=$(date +%y%m%d)
LOG_STREAM_NAME="agent-$TIMESTAMP"
echo "📋 Log stream will be auto-created: $LOG_STREAM_NAME"

# Generate .env configuration
ENV_FILE="chatbot-app/backend/.env"
echo "📋 Generating observability configuration..."

cat > "$ENV_FILE" << EOF
# AWS Distro for OpenTelemetry (ADOT) Configuration
OTEL_PYTHON_DISTRO=aws_distro
OTEL_PYTHON_CONFIGURATOR=aws_configurator
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=http/protobuf
OTEL_LOGS_EXPORTER=otlp
OTEL_TRACES_EXPORTER=otlp

# CloudWatch Integration - log stream will be auto-created
OTEL_EXPORTER_OTLP_LOGS_HEADERS=x-aws-log-group=$LOG_GROUP_NAME,x-aws-metric-namespace=agentsd
OTEL_RESOURCE_ATTRIBUTES=service.name=strands-chatbot

# Enable AgentCore Observability
AGENT_OBSERVABILITY_ENABLED=true
AWS_REGION=$AWS_REGION

# Ultra-fast batch processing for real-time traces
OTEL_BSP_SCHEDULE_DELAY=100 
OTEL_BSP_MAX_EXPORT_BATCH_SIZE=1
OTEL_BSP_EXPORT_TIMEOUT=5000
EOF

echo "✅ Created $ENV_FILE with observability configuration"

echo ""
echo "🎯 Next Steps:"
echo "1. Enable CloudWatch Transaction Search:"
echo "   - Open CloudWatch Console → Application Signals (APM) → Transaction search"
echo "   - Choose 'Enable Transaction Search'"
echo "   - Select 'ingest spans as structured logs'"
echo "   - Choose 'Save'"
echo ""
echo "2. Start the application:"
echo "   cd chatbot-app && ./start.sh"
echo ""
echo "3. View traces in CloudWatch:"
echo "   - CloudWatch Console → Application Signals → Traces"
echo "   - CloudWatch Console → GenAI Observability Dashboard"
echo "   - Filter by service.name = 'strands-chatbot'"
echo ""
echo "✅ AgentCore Observability setup complete!"