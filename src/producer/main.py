import os
import json
import time
import random
import signal
import logging
from confluent_kafka import Producer
from dotenv import load_dotenv
from faker import Faker
from jsonschema import ValidationError, validate, FormatChecker
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

logging.basicConfig(
    format="%(asctime)s.%(msecs)03d %(levelname)s: %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)
load_dotenv(dotenv_path="/app/.env")

fake = Faker()

TRANSACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "transaction_id": {"type": "string"},
        "user_id": {"type": "number", "minimum": 1000, "maximum": 9999},
        "amount": {"type": "number", "minimum": 0.01, "maximum": 10000},
        "currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
        "merchant": {"type": "string"},
        "timestamp": {"type": "string", "format": "date-time"},
        "location": {"type": "string", "pattern": "^[A-Z]{2}$"},
        "is_fraud": {"type": "integer", "minimum": 0, "maximum": 1}
    },
    "required": ["transaction_id", "user_id", "amount", "currency", "timestamp","is_fraud"]
}

class TransactionProducer:
    def __init__(self):
        self.bootstrap_servers = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'localhost:9092')
        self.kafka_username = os.getenv('KAFKA_USERNAME')
        self.kafka_password = os.getenv('KAFKA_PASSWORD')
        self.topic = os.getenv('KAFKA_TOPIC', 'transactions')
        self.running = False

        # Confluent Kafka config
        self.producer_config = {
            'bootstrap.servers': self.bootstrap_servers,
            'client.id': 'transaction-producer',
            'compression.type': 'gzip',
            'linger.ms': '5',
            'batch.size': 16384
        }

        if self.kafka_username and self.kafka_password:
            self.producer_config.update({
                'security.protocol': 'SASL_SSL',
                'sasl.mechanisms': 'PLAIN',
                'sasl.username': self.kafka_username,
                'sasl.password': self.kafka_password,
            })
        else:
            self.producer_config['security.protocol'] = 'PLAINTEXT'

        try:
            self.producer = Producer(self.producer_config)
            logger.info('Confluent Kafka producer started')
        except Exception as e:
            logger.error(f'Failed to initialize Confluent Kafka producer: {str(e)}')
            raise e

        self.compromised_users = set(random.sample(range(1000, 9999), k=2000))  # 0.5% of users
        self.high_risk_merchants = ['QuickCash', 'GlobalDigital', 'FastMoneyX']
        self.fraud_pattern_weights = {
            'account_takeover': 0.4,
            'card_testing': 0.3,
            'merchant_collusion': 0.2,
            'geo_anomaly': 0.1
        }

        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)

    def validate_transaction(self, transaction: Dict[str, Any]) -> bool:
        try:
            validate(instance=transaction, schema=TRANSACTION_SCHEMA, format_checker=FormatChecker())
            return True
        except ValidationError as e:
            logger.error(f'Invalid transaction: {e.message}')
            return False

    def shutdown(self, signum=None, frame=None):
        if self.running:
            logger.info('Initiating shutdown...')
            self.running = False
        if self.producer:
            self.producer.flush(timeout=30)  # Ensure all messages are delivered before shutdown
        logger.info('Producer stopped')

    def delivery_report(self, err, msg):
        if err is not None:
            logger.error(f'Message delivery failed: {err}')
        else:
            logger.info(f'Message delivered to {msg.topic()} [{msg.partition()}]')

    def generate_transaction(self) -> Optional[Dict[str, Any]]:
        transaction = {
            'transaction_id': fake.uuid4(),
            'user_id': random.randint(1000, 9999),
            'amount': round(fake.pyfloat(min_value=0.01, max_value=10000), 2),
            'currency': "USD",
            'merchant': fake.company(),
            'timestamp': (datetime.now(timezone.utc) + timedelta(seconds=random.randint(-300, 0))).isoformat(),
            'location': fake.country_code(),
            'is_fraud': 0
        }

        is_fraud = 0
        amount = transaction['amount']
        user_id = transaction['user_id']
        merchant = transaction['merchant']

        if user_id in self.compromised_users and amount > 500:
            if random.random() < 0.3:
                is_fraud = 1
                transaction['amount'] = round(random.uniform(500, 5000), 2)
                transaction['merchant'] = random.choice(self.high_risk_merchants)

        if not is_fraud and amount < 2.0:
            if user_id % 1000 == 0 and random.random() < 0.25:
                is_fraud = 1
                transaction['amount'] = round(random.uniform(0.01, 2), 2)
                transaction['location'] = 'US'

        if not is_fraud and merchant in self.high_risk_merchants:
            if amount > 3000 and random.random() < 0.15:
                is_fraud = 1
                transaction['amount'] = round(random.uniform(300, 1500), 2)

        if not is_fraud and user_id % 500 == 0 and random.random() < 0.1:
            is_fraud = 1
            transaction['location'] = random.choice(['CN', 'RU', 'GB'])

        if not is_fraud and random.random() < 0.2:
            is_fraud = 1
            transaction['amount'] = round(random.uniform(100, 2000), 2)

        transaction['is_fraud'] = is_fraud

        if self.validate_transaction(transaction):
            return transaction
        return None

    def send_transaction(self, transaction) -> bool:
        if not transaction:
            return False
        self.producer.produce(
            self.topic,
            key=transaction['transaction_id'],
            value=json.dumps(transaction),
            callback=self.delivery_report
        )
        self.producer.poll(0)
        return True

    def run_continuous_production(self, interval: float = 1.0):
        self.running = True
        logger.info('Starting producer for topic %s...', self.topic)
        try:
            while self.running:
                transaction = self.generate_transaction()
                if transaction:
                    self.send_transaction(transaction)
                    time.sleep(interval)
        finally:
            self.shutdown()

if __name__ == "__main__":
    producer = TransactionProducer()
    producer.run_continuous_production()