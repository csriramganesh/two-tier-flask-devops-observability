import os
import time

from flask import Flask, render_template, request, jsonify, Response, g
from flask_mysqldb import MySQL

from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    generate_latest,
    CONTENT_TYPE_LATEST
)


app = Flask(__name__)

# --------------------------------------------------
# MySQL Configuration
# --------------------------------------------------

app.config['MYSQL_HOST'] = os.environ.get('MYSQL_HOST', 'localhost')
app.config['MYSQL_USER'] = os.environ.get('MYSQL_USER', 'default_user')
app.config['MYSQL_PASSWORD'] = os.environ.get(
    'MYSQL_PASSWORD',
    'default_password'
)
app.config['MYSQL_DB'] = os.environ.get('MYSQL_DB', 'default_db')

mysql = MySQL(app)


# ==================================================
# Prometheus Metrics
# ==================================================

# 1. Total HTTP requests
HTTP_REQUESTS_TOTAL = Counter(
    'flask_http_requests_total',
    'Total number of HTTP requests',
    ['method', 'endpoint', 'status']
)


# 2. HTTP request latency
HTTP_REQUEST_DURATION = Histogram(
    'flask_http_request_duration_seconds',
    'HTTP request latency in seconds',
    ['method', 'endpoint']
)


# 3. Requests currently being processed
HTTP_REQUESTS_IN_PROGRESS = Gauge(
    'flask_http_requests_inprogress',
    'Number of HTTP requests currently being processed'
)


# 4. HTTP 5xx errors
HTTP_ERRORS_TOTAL = Counter(
    'flask_http_errors_total',
    'Total number of HTTP 5xx errors',
    ['endpoint']
)


# 5. Database query latency
DB_QUERY_DURATION = Histogram(
    'flask_db_query_duration_seconds',
    'Database query duration in seconds',
    ['operation']
)


# 6. Database failures
DB_ERRORS_TOTAL = Counter(
    'flask_db_errors_total',
    'Total number of database errors',
    ['operation']
)


# 7. Successfully created messages
MESSAGES_CREATED_TOTAL = Counter(
    'flask_messages_created_total',
    'Total number of messages successfully inserted'
)


# 8. Application information
APP_INFO = Gauge(
    'flask_app_info',
    'Application information',
    ['version']
)

APP_INFO.labels(version='v1.0.0').set(1)


# ==================================================
# Database Initialization
# ==================================================

def init_db():

    with app.app_context():

        cur = mysql.connection.cursor()

        cur.execute(
            '''
            CREATE TABLE IF NOT EXISTS messages (
                id INT AUTO_INCREMENT PRIMARY KEY,
                message TEXT
            );
            '''
        )

        mysql.connection.commit()

        cur.close()


# ==================================================
# Request Monitoring
# ==================================================

@app.before_request
def before_request():

    # Do not include Prometheus scrape requests
    if request.path == '/metrics':
        return

    g.request_start_time = time.time()

    HTTP_REQUESTS_IN_PROGRESS.inc()


@app.after_request
def after_request(response):

    if request.path == '/metrics':
        return response

    endpoint = request.path

    method = request.method

    status = str(response.status_code)

    # Count request
    HTTP_REQUESTS_TOTAL.labels(
        method=method,
        endpoint=endpoint,
        status=status
    ).inc()

    # Measure request duration
    if hasattr(g, 'request_start_time'):

        duration = time.time() - g.request_start_time

        HTTP_REQUEST_DURATION.labels(
            method=method,
            endpoint=endpoint
        ).observe(duration)

    # Count application errors
    if response.status_code >= 500:

        HTTP_ERRORS_TOTAL.labels(
            endpoint=endpoint
        ).inc()

    HTTP_REQUESTS_IN_PROGRESS.dec()

    return response


# ==================================================
# Application Routes
# ==================================================

@app.route('/')
def hello():

    operation = 'select_messages'

    try:

        with DB_QUERY_DURATION.labels(
            operation=operation
        ).time():

            cur = mysql.connection.cursor()

            cur.execute(
                'SELECT message FROM messages'
            )

            messages = cur.fetchall()

            cur.close()

        return render_template(
            'index.html',
            messages=messages
        )

    except Exception:

        DB_ERRORS_TOTAL.labels(
            operation=operation
        ).inc()

        raise


@app.route('/submit', methods=['POST'])
def submit():

    operation = 'insert_message'

    new_message = request.form.get('new_message')

    try:

        with DB_QUERY_DURATION.labels(
            operation=operation
        ).time():

            cur = mysql.connection.cursor()

            cur.execute(
                'INSERT INTO messages (message) VALUES (%s)',
                [new_message]
            )

            mysql.connection.commit()

            cur.close()

        MESSAGES_CREATED_TOTAL.inc()

        return jsonify({
            'message': new_message
        })

    except Exception:

        DB_ERRORS_TOTAL.labels(
            operation=operation
        ).inc()

        raise


# ==================================================
# Kubernetes Health Endpoints
# ==================================================

@app.route('/health')
def health():

    return jsonify({
        'status': 'healthy'
    }), 200


@app.route('/ready')
def readiness():

    operation = 'readiness_check'

    try:

        with DB_QUERY_DURATION.labels(
            operation=operation
        ).time():

            cur = mysql.connection.cursor()

            cur.execute('SELECT 1')

            cur.fetchone()

            cur.close()

        return jsonify({
            'status': 'ready',
            'database': 'connected'
        }), 200

    except Exception:

        DB_ERRORS_TOTAL.labels(
            operation=operation
        ).inc()

        return jsonify({
            'status': 'not-ready',
            'database': 'disconnected'
        }), 503


# ==================================================
# Prometheus Metrics Endpoint
# ==================================================

@app.route('/metrics')
def metrics():

    return Response(
        generate_latest(),
        mimetype=CONTENT_TYPE_LATEST
    )


# ==================================================
# Start Application
# ==================================================

if __name__ == '__main__':

    init_db()

    app.run(
        host='0.0.0.0',
        port=5000,
        debug=False
    )
