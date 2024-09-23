import os
import hashlib
import psycopg2
import re
import logging
import colorlog

# Configure logger with colorlog
logger = colorlog.getLogger(__name__)
handler = colorlog.StreamHandler()
handler.setFormatter(
    colorlog.ColoredFormatter(
        "%(log_color)s%(levelname)s:%(name)s:%(message)s",
        log_colors={
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "bold_red",
        },
    )
)
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)

def _calculate_md5(file_path):
    """Calculate the MD5 hash of a given file."""
    logger.debug(f"Calculating MD5 for {file_path}")
    md5_hash = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                md5_hash.update(chunk)
        return md5_hash.hexdigest()
    except Exception as e:
        logger.error(f"Error calculating MD5 for {file_path}: {e}")
        return None

def _detect_category(file_path, file_extension):
    """Detect the file's category based on its path or file extension."""
    logger.debug(f"Detecting category for {file_path}")
    categories = {
        "movie": ["movie", "movies"],
        "tv show": ["tv", "show", "shows", "series"],
        "book": ["book", "books"],
        "audiobook": ["audiobook", "audiobooks"],
    }

    # First, check the path for keywords
    for category, keywords in categories.items():
        if any(keyword in file_path.lower() for keyword in keywords):
            logger.info(f"Category detected from path: {category}")
            return category

    # Use file extension for categorization
    extension_categories = {
        "audio": [".mp3", ".wav", ".flac"],
        "video": [".mp4", ".avi", ".mkv", ".mov"],
        "picture": [".jpg", ".jpeg", ".png", ".gif", ".bmp"],
        "plaintext": [".txt", ".md", ".log"],
        "document": [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"],
    }

    for category, extensions in extension_categories.items():
        if file_extension.lower() in extensions:
            logger.info(f"Category detected from file extension: {category}")
            return category

    logger.warning(f"No category matched for {file_path}. Categorized as 'other'")
    return "other"

def _load_exclusion_patterns(directory):
    """Load the .exclude_patterns file if it exists in the directory."""
    logger.debug(f"Loading exclusion patterns from {directory}")
    exclude_file_path = os.path.join(directory, ".exclude_patterns")
    if os.path.exists(exclude_file_path):
        try:
            with open(exclude_file_path, "r") as f:
                logger.info(f"Exclusion patterns loaded from {exclude_file_path}")
                return [re.compile(line.strip()) for line in f.readlines() if line.strip()]
        except Exception as e:
            logger.error(f"Error loading exclusion patterns: {e}")
    return []

def _should_exclude(file_path, exclude_patterns):
    """Check if a file should be excluded based on the regex patterns."""
    for pattern in exclude_patterns:
        if pattern.search(file_path):
            logger.info(f"File {file_path} excluded by pattern {pattern.pattern}")
            return True
    return False

def _create_database(connection):
    """Create the indexed_files table if it doesn't exist."""
    logger.debug("Creating database table if not exists")
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS indexed_files (
                id SERIAL PRIMARY KEY,
                file_name VARCHAR(255),
                path TEXT,
                md5_hash VARCHAR(32) UNIQUE,
                file_size BIGINT,
                category VARCHAR(100),
                date_indexed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            connection.commit()
            logger.info("Database table created or already exists.")
    except Exception as e:
        logger.critical(f"Error creating database table: {e}")

def _index_directory(directory, exclude_patterns, connection):
    """Index the files in the given directory and its subdirectories."""
    logger.debug(f"Indexing directory: {directory}")
    file_index = []

    for root, _, files in os.walk(directory):
        for file_name in files:
            if file_name == ".exclude_patterns":
                continue  # Skip the .exclude_patterns file itself

            file_path = os.path.join(root, file_name)

            # Skip files that match the exclusion patterns
            if _should_exclude(file_path, exclude_patterns):
                continue

            # Get file size and extension
            file_size = os.path.getsize(file_path)
            file_extension = os.path.splitext(file_name)[1]

            # Check if the file's MD5 hash already exists in the database
            file_hash = _calculate_md5(file_path)
            if file_hash is None:
                logger.error(f"Skipping {file_path} due to MD5 error")
                continue

            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM indexed_files WHERE md5_hash = %s;", (file_hash,))
                existing_file = cursor.fetchone()

            if existing_file:
                logger.info(f"File already indexed: {file_name} (MD5: {file_hash})")
                continue  # Skip recalculation if the hash exists

            # Detect the file category
            file_category = _detect_category(file_path, file_extension)

            # Insert the new file entry into the database
            with connection.cursor() as cursor:
                cursor.execute("""
                INSERT INTO indexed_files (file_name, path, md5_hash, file_size, category)
                VALUES (%s, %s, %s, %s, %s);
                """, (file_name, file_path, file_hash, file_size, file_category))
                connection.commit()

            # Add to the index
            file_index.append({
                "file_name": file_name,
                "path": file_path,
                "md5_hash": file_hash,
                "file_size": file_size,
                "category": file_category,
            })
            logger.info(f"Indexed {file_name} with category {file_category}")

    return file_index

def indexer(directory):
    """
    Index the files in the given directory, exclude files based on .exclude_patterns,
    and store the index in a PostgreSQL database.
    
    Args:
        directory (str): The directory to index.
    """
    logger.debug(f"Starting indexing for directory {directory}")
    try:
        connection = psycopg2.connect(
            dbname=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            host=os.getenv('DB_HOST', 'localhost'),
            port=os.getenv('DB_PORT', '5432')
        )
        logger.info("Connected to the database successfully.")
    except Exception as e:
        logger.critical(f"Database connection error: {e}")
        return

    # Create the database table if it doesn't exist
    _create_database(connection)

    # Load exclusion patterns from the parent directory
    exclude_patterns = _load_exclusion_patterns(directory)

    # Index the directory
    file_index = _index_directory(directory, exclude_patterns, connection)

    logger.info(f"Indexing complete. {len(file_index)} files indexed.")

    # Close the database connection
    connection.close()
    logger.debug("Database connection closed.")

# Example usage
# indexer("/path/to/directory")

