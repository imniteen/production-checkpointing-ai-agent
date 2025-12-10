# Production Deployment Guide

This guide covers deploying the state management demo to production environments (AWS, Azure, GCP).

## 🎯 Production Architecture

```
                    ┌─────────────┐
                    │   Load      │
                    │  Balancer   │
                    └──────┬──────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
   ┌────▼────┐       ┌────▼────┐       ┌────▼────┐
   │ FastAPI │       │ FastAPI │       │ FastAPI │
   │Instance │       │Instance │       │Instance │
   └────┬────┘       └────┬────┘       └────┬────┘
        │                  │                  │
        └──────────────────┼──────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
   ┌────▼────────┐   ┌────▼──────────┐  ┌───▼────────┐
   │ PostgreSQL  │   │ Elasticsearch │  │   Redis    │
   │  (Primary)  │   │  (Secondary)  │  │  (Cache)   │
   └─────────────┘   └───────────────┘  └────────────┘
```

---

## 🔐 Security Checklist

### 1. Database Security

#### PostgreSQL

```bash
# Use SSL/TLS connections
connection_string = (
    f"postgresql://{user}:{password}@{host}:{port}/{db}"
    f"?sslmode=require"
)

# Enable row-level security
CREATE POLICY user_isolation ON checkpoints
    USING (thread_id LIKE user_id || ':%');
```

#### Elasticsearch

```yaml
# elasticsearch.yml
xpack.security.enabled: true
xpack.security.transport.ssl.enabled: true
xpack.security.http.ssl.enabled: true
```

### 2. Credentials Management

**AWS Secrets Manager:**
```python
import boto3
import json

def get_secret(secret_name):
    client = boto3.client('secretsmanager', region_name='us-east-1')
    response = client.get_secret_value(SecretId=secret_name)
    return json.loads(response['SecretString'])

# Usage
db_creds = get_secret('prod/agent/postgresql')
connection_string = (
    f"postgresql://{db_creds['username']}:{db_creds['password']}"
    f"@{db_creds['host']}:{db_creds['port']}/{db_creds['database']}"
)
```

**Azure Key Vault:**
```python
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

credential = DefaultAzureCredential()
client = SecretClient(vault_url="https://myvault.vault.azure.net/", credential=credential)

db_password = client.get_secret("postgres-password").value
```

### 3. Network Security

```bash
# Restrict PostgreSQL to private VPC only
Security Group Rules:
- Inbound: Port 5432 from application security group only
- Outbound: None

# Use VPC endpoints for AWS services
- RDS: Private subnet only
- Elasticsearch: VPC endpoint
```

### 4. Encryption

```python
# Encrypt sensitive fields before storing
from cryptography.fernet import Fernet

key = os.getenv("ENCRYPTION_KEY").encode()
cipher = Fernet(key)

def encrypt_field(value: str) -> str:
    return cipher.encrypt(value.encode()).decode()

def decrypt_field(encrypted: str) -> str:
    return cipher.decrypt(encrypted.encode()).decode()

# Usage in state
state["customer_email"] = encrypt_field(email)
```

---

## ☁️ Cloud Platform Deployments

### AWS Deployment

#### 1. Infrastructure Setup (Terraform)

```hcl
# main.tf
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

# RDS PostgreSQL
resource "aws_db_instance" "agent_postgres" {
  identifier           = "agent-postgres-prod"
  engine              = "postgres"
  engine_version      = "15.4"
  instance_class      = "db.t3.medium"
  allocated_storage   = 100
  storage_type        = "gp3"
  storage_encrypted   = true
  
  db_name  = "agent_state"
  username = var.db_username
  password = var.db_password
  
  vpc_security_group_ids = [aws_security_group.rds.id]
  db_subnet_group_name   = aws_db_subnet_group.main.name
  
  backup_retention_period = 7
  backup_window          = "03:00-04:00"
  maintenance_window     = "mon:04:00-mon:05:00"
  
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
  
  tags = {
    Name        = "agent-postgres-prod"
    Environment = "production"
  }
}

# Elasticsearch (OpenSearch)
resource "aws_elasticsearch_domain" "agent_search" {
  domain_name           = "agent-search-prod"
  elasticsearch_version = "OpenSearch_2.11"
  
  cluster_config {
    instance_type  = "t3.medium.elasticsearch"
    instance_count = 2
  }
  
  ebs_options {
    ebs_enabled = true
    volume_size = 100
    volume_type = "gp3"
  }
  
  encrypt_at_rest {
    enabled = true
  }
  
  node_to_node_encryption {
    enabled = true
  }
  
  vpc_options {
    subnet_ids         = [aws_subnet.private_a.id, aws_subnet.private_b.id]
    security_group_ids = [aws_security_group.elasticsearch.id]
  }
  
  tags = {
    Name        = "agent-search-prod"
    Environment = "production"
  }
}

# ECS Fargate for FastAPI
resource "aws_ecs_cluster" "agent" {
  name = "agent-cluster-prod"
  
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_task_definition" "agent_api" {
  family                   = "agent-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  
  container_definitions = jsonencode([{
    name  = "agent-api"
    image = "${var.ecr_repository_url}:latest"
    
    portMappings = [{
      containerPort = 8000
      protocol      = "tcp"
    }]
    
    environment = [
      {
        name  = "POSTGRES_HOST"
        value = aws_db_instance.agent_postgres.address
      },
      {
        name  = "ELASTICSEARCH_URL"
        value = "https://${aws_elasticsearch_domain.agent_search.endpoint}"
      }
    ]
    
    secrets = [
      {
        name      = "OPENAI_API_KEY"
        valueFrom = aws_secretsmanager_secret.openai_key.arn
      },
      {
        name      = "POSTGRES_PASSWORD"
        valueFrom = aws_secretsmanager_secret.db_password.arn
      }
    ]
    
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = "/ecs/agent-api"
        "awslogs-region"        = "us-east-1"
        "awslogs-stream-prefix" = "ecs"
      }
    }
  }])
}

# Application Load Balancer
resource "aws_lb" "agent" {
  name               = "agent-alb-prod"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = [aws_subnet.public_a.id, aws_subnet.public_b.id]
}
```

#### 2. Deployment Script

```bash
#!/bin/bash
# deploy-aws.sh

set -e

# Build Docker image
docker build -t agent-api:latest .

# Tag for ECR
ECR_REPO="123456789012.dkr.ecr.us-east-1.amazonaws.com/agent-api"
docker tag agent-api:latest $ECR_REPO:latest
docker tag agent-api:latest $ECR_REPO:$(git rev-parse --short HEAD)

# Push to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin $ECR_REPO
docker push $ECR_REPO:latest
docker push $ECR_REPO:$(git rev-parse --short HEAD)

# Update ECS service
aws ecs update-service \
  --cluster agent-cluster-prod \
  --service agent-api \
  --force-new-deployment \
  --region us-east-1

echo "✅ Deployment complete"
```

---

### Azure Deployment

#### 1. Infrastructure (ARM Template)

```json
{
  "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
  "contentVersion": "1.0.0.0",
  "resources": [
    {
      "type": "Microsoft.DBforPostgreSQL/flexibleServers",
      "apiVersion": "2021-06-01",
      "name": "agent-postgres-prod",
      "location": "[resourceGroup().location]",
      "sku": {
        "name": "Standard_D2s_v3",
        "tier": "GeneralPurpose"
      },
      "properties": {
        "administratorLogin": "agentadmin",
        "version": "15",
        "storage": {
          "storageSizeGB": 128
        },
        "backup": {
          "backupRetentionDays": 7,
          "geoRedundantBackup": "Enabled"
        },
        "highAvailability": {
          "mode": "ZoneRedundant"
        }
      }
    },
    {
      "type": "Microsoft.ContainerInstance/containerGroups",
      "apiVersion": "2021-09-01",
      "name": "agent-api",
      "location": "[resourceGroup().location]",
      "properties": {
        "containers": [
          {
            "name": "agent-api",
            "properties": {
              "image": "agentregistry.azurecr.io/agent-api:latest",
              "resources": {
                "requests": {
                  "cpu": 2,
                  "memoryInGB": 4
                }
              },
              "ports": [
                {
                  "port": 8000
                }
              ],
              "environmentVariables": [
                {
                  "name": "POSTGRES_HOST",
                  "value": "[reference('agent-postgres-prod').fullyQualifiedDomainName]"
                }
              ]
            }
          }
        ],
        "osType": "Linux",
        "ipAddress": {
          "type": "Public",
          "ports": [
            {
              "protocol": "TCP",
              "port": 8000
            }
          ]
        }
      }
    }
  ]
}
```

#### 2. Deployment Script

```bash
#!/bin/bash
# deploy-azure.sh

set -e

RESOURCE_GROUP="agent-prod-rg"
LOCATION="eastus"
ACR_NAME="agentregistry"

# Build and push to ACR
az acr build \
  --registry $ACR_NAME \
  --image agent-api:latest \
  --image agent-api:$(git rev-parse --short HEAD) \
  --file Dockerfile \
  .

# Deploy ARM template
az deployment group create \
  --resource-group $RESOURCE_GROUP \
  --template-file azure-template.json \
  --parameters @azure-parameters.json

echo "✅ Azure deployment complete"
```

---

### GCP Deployment

#### 1. Infrastructure (Terraform)

```hcl
# gcp-main.tf

# Cloud SQL PostgreSQL
resource "google_sql_database_instance" "agent_postgres" {
  name             = "agent-postgres-prod"
  database_version = "POSTGRES_15"
  region           = "us-central1"
  
  settings {
    tier = "db-custom-2-8192"
    
    backup_configuration {
      enabled            = true
      start_time         = "03:00"
      point_in_time_recovery_enabled = true
    }
    
    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.agent_vpc.id
    }
    
    database_flags {
      name  = "max_connections"
      value = "100"
    }
  }
}

# Cloud Run for FastAPI
resource "google_cloud_run_service" "agent_api" {
  name     = "agent-api"
  location = "us-central1"
  
  template {
    spec {
      containers {
        image = "gcr.io/${var.project_id}/agent-api:latest"
        
        resources {
          limits = {
            cpu    = "2000m"
            memory = "4Gi"
          }
        }
        
        env {
          name  = "POSTGRES_HOST"
          value = google_sql_database_instance.agent_postgres.private_ip_address
        }
        
        env {
          name = "POSTGRES_PASSWORD"
          value_from {
            secret_key_ref {
              name = google_secret_manager_secret.db_password.secret_id
              key  = "latest"
            }
          }
        }
      }
      
      service_account_name = google_service_account.agent_api.email
    }
    
    metadata {
      annotations = {
        "autoscaling.knative.dev/maxScale" = "10"
        "autoscaling.knative.dev/minScale" = "2"
      }
    }
  }
  
  traffic {
    percent         = 100
    latest_revision = true
  }
}
```

---

## 📊 Monitoring & Observability

### CloudWatch (AWS)

```python
# Add to agent.py
import boto3

cloudwatch = boto3.client('cloudwatch')

def publish_metric(metric_name: str, value: float, unit: str = "Count"):
    cloudwatch.put_metric_data(
        Namespace='Agent/StateManagement',
        MetricData=[
            {
                'MetricName': metric_name,
                'Value': value,
                'Unit': unit,
                'Timestamp': datetime.utcnow()
            }
        ]
    )

# Usage
publish_metric('CheckpointWriteLatency', duration_ms, 'Milliseconds')
publish_metric('HITLEscalationRate', 1, 'Count')
```

### Application Insights (Azure)

```python
from opencensus.ext.azure import metrics_exporter
from opencensus.stats import aggregation as aggregation_module
from opencensus.stats import measure as measure_module
from opencensus.stats import stats as stats_module
from opencensus.stats import view as view_module

# Initialize
exporter = metrics_exporter.new_metrics_exporter(
    connection_string=os.getenv('APPLICATIONINSIGHTS_CONNECTION_STRING')
)

# Create measures
checkpoint_latency = measure_module.MeasureFloat(
    "checkpoint_write_latency",
    "Checkpoint write latency",
    "ms"
)

# Record metrics
stats = stats_module.stats
mmap = stats.stats_recorder.new_measurement_map()
mmap.measure_float_put(checkpoint_latency, duration_ms)
mmap.record()
```

### Cloud Monitoring (GCP)

```python
from google.cloud import monitoring_v3

client = monitoring_v3.MetricServiceClient()
project_name = f"projects/{project_id}"

def write_metric(metric_type: str, value: float):
    series = monitoring_v3.TimeSeries()
    series.metric.type = f"custom.googleapis.com/{metric_type}"
    
    point = monitoring_v3.Point()
    point.value.double_value = value
    point.interval.end_time.FromDatetime(datetime.utcnow())
    
    series.points = [point]
    client.create_time_series(name=project_name, time_series=[series])
```

---

## 🚀 Performance Optimization

### 1. Connection Pooling

```python
# Increase pool size for production
connection_string = (
    f"postgresql://{user}:{password}@{host}:{port}/{db}"
    f"?min_size=20&max_size=100"
)
```

### 2. Database Indexing

```sql
-- Essential indexes
CREATE INDEX CONCURRENTLY idx_thread_id_created 
ON checkpoints(thread_id, created_at DESC);

CREATE INDEX CONCURRENTLY idx_user_sessions 
ON checkpoints((split_part(thread_id, ':', 1)), created_at DESC);

-- Partial index for active sessions
CREATE INDEX CONCURRENTLY idx_awaiting_human 
ON checkpoints((checkpoint->>'awaiting_human_input'))
WHERE checkpoint->>'awaiting_human_input' = 'true';
```

### 3. Table Partitioning

```sql
-- Partition by month for large-scale deployments
CREATE TABLE checkpoints_2024_01 PARTITION OF checkpoints
FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');

CREATE TABLE checkpoints_2024_02 PARTITION OF checkpoints
FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');

-- Automated partitioning with pg_partman
```

### 4. Redis Caching Layer

```python
import redis.asyncio as redis

redis_client = redis.Redis(
    host=os.getenv("REDIS_HOST"),
    port=6379,
    decode_responses=True
)

async def get_state_cached(thread_id: str, config: dict):
    # Try cache first
    cached = await redis_client.get(f"state:{thread_id}")
    if cached:
        return json.loads(cached)
    
    # Fallback to PostgreSQL
    state = await checkpointer.aget(config)
    if state:
        # Cache for 5 minutes
        await redis_client.setex(
            f"state:{thread_id}",
            300,
            json.dumps(state)
        )
    return state
```

---

## 🔄 Backup & Disaster Recovery

### Automated Backups

```bash
# PostgreSQL backup script
#!/bin/bash
BACKUP_DIR="/backups/postgres"
DATE=$(date +%Y%m%d_%H%M%S)

pg_dump -h $POSTGRES_HOST -U $POSTGRES_USER -d agent_state \
  | gzip > $BACKUP_DIR/agent_state_$DATE.sql.gz

# Upload to S3
aws s3 cp $BACKUP_DIR/agent_state_$DATE.sql.gz \
  s3://my-backups/postgres/

# Cleanup old backups (keep 30 days)
find $BACKUP_DIR -name "*.sql.gz" -mtime +30 -delete
```

### Point-in-Time Recovery

```sql
-- Enable PITR in PostgreSQL
ALTER SYSTEM SET wal_level = 'replica';
ALTER SYSTEM SET archive_mode = 'on';
ALTER SYSTEM SET archive_command = 'test ! -f /archive/%f && cp %p /archive/%f';

-- Restore to specific time
pg_restore --dbname=agent_state_restored \
  --create --format=custom \
  --time="2024-12-10 14:30:00" \
  backup_file.dump
```

---

## 📈 Scaling Strategies

### Horizontal Scaling

1. **Stateless API Servers:** Add more FastAPI/Cloud Run instances
2. **Read Replicas:** PostgreSQL read replicas for analytics
3. **Sharding:** Partition by user_id for extreme scale

### Vertical Scaling

1. **Database:** Increase instance size (CPU/RAM)
2. **Connection Pool:** Tune min/max connections
3. **Caching:** Add Redis layer

---

## 🛡️ Health Checks

```python
from fastapi import FastAPI

@app.get("/health")
async def health_check():
    checks = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "checks": {}
    }
    
    # PostgreSQL check
    try:
        checkpointer = await get_checkpointer()
        checks["checks"]["postgresql"] = "healthy"
    except Exception as e:
        checks["checks"]["postgresql"] = f"unhealthy: {e}"
        checks["status"] = "unhealthy"
    
    # Elasticsearch check
    try:
        es_client = await get_elasticsearch()
        if es_client:
            await es_client.ping()
            checks["checks"]["elasticsearch"] = "healthy"
        else:
            checks["checks"]["elasticsearch"] = "disabled"
    except Exception as e:
        checks["checks"]["elasticsearch"] = f"unhealthy: {e}"
    
    return checks
```

---

## 📚 Additional Resources

- [AWS Well-Architected Framework](https://aws.amazon.com/architecture/well-architected/)
- [Azure Architecture Center](https://docs.microsoft.com/en-us/azure/architecture/)
- [GCP Architecture Framework](https://cloud.google.com/architecture/framework)
- [PostgreSQL Performance Tuning](https://wiki.postgresql.org/wiki/Performance_Optimization)
- [Elasticsearch Production Best Practices](https://www.elastic.co/guide/en/elasticsearch/reference/current/setup.html)

---

**Need help?** Open an issue or reach out to the team.
