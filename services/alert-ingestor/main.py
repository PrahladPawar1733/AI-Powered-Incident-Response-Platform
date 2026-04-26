from contextlib import asynccontextmanager
from fastapi import FastAPI
from shared.config import settings
from shared.logger import configure_logging, get_logger
from shared.redis_client import init_redis
from shared.kafka_utils import KafkaProducer

from routes import prometheus, grafana, manual, webhook

# Configure structured logging for the service
configure_logging(settings.service_name)
log = get_logger("alert-ingestor")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifecycle manager for FastAPI.
    Initializes shared resources (Redis, Kafka) on startup
    and cleans them up on shutdown.
    """
    log.info("service_starting", service=settings.service_name)
    
    try:
        # Initialize Redis
        app.state.redis = await init_redis(settings.redis_url)
        log.info("redis_connected", url=settings.redis_url)
        
        # Initialize Kafka Producer
        app.state.kafka = KafkaProducer(settings.kafka_bootstrap_servers)
        log.info("kafka_producer_initialized", brokers=settings.kafka_bootstrap_servers)
        
        log.info("service_started")
        yield
        
    except Exception as e:
        log.error("service_startup_failed", error=str(e))
        raise
    finally:
        log.info("service_stopping")
        # Cleanup
        if hasattr(app.state, "redis"):
            await app.state.redis.close()
        if hasattr(app.state, "kafka"):
            app.state.kafka.flush()
        log.info("service_stopped")


app = FastAPI(
    title="Alert Ingestor",
    description="Receives alerts, normalizes them, and publishes to Kafka.",
    lifespan=lifespan
)

# Register routes under /alerts
app.include_router(prometheus.router, prefix="/alerts")
app.include_router(grafana.router, prefix="/alerts")
app.include_router(manual.router, prefix="/alerts")
app.include_router(webhook.router, prefix="/alerts")

@app.get("/health")
async def health_check():
    """Health check endpoint for Docker/Kubernetes."""
    return {
        "status": "healthy",
        "service": settings.service_name,
        "redis": hasattr(app.state, "redis"),
        "kafka": hasattr(app.state, "kafka")
    }
