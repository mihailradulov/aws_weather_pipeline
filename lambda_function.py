import json
import logging
import os
import urllib.request
from datetime import datetime, timezone
import boto3

# Configure structured logging for AWS CloudWatch
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS S3 client
s3_client = boto3.client('s3')

# Retrieve environment variables
API_KEY = os.getenv("OPENWEATHER_API_KEY")
BUCKET_NAME = os.getenv("S3_BUCKET_NAME")

# Target list of cities with coordinates
CITIES = [
    {"name": "Sofia", "lat": 42.6977, "lon": 23.3217},
    {"name": "Samokov", "lat": 42.3377, "lon": 23.5592},
    {"name": "Relyovo", "lat": 42.3721, "lon": 23.44815},
    {"name": "Pazardzhik", "lat": 42.200001, "lon": 24.33333},
    {"name": "Krapets", "lat": 43.5667, "lon": 28.5668},
    {"name": "Ognyanovo", "lat": 41.6126, "lon": 23.7897}
]


def get_weather_raw(lat: float, lon: float) -> dict:
    # Fetch raw weather JSON data from OpenWeatherMap API
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={API_KEY}&units=metric"
    req = urllib.request.Request(url, headers={'User-Agent': 'AWS-Lambda-Weather-ETL'})
    
    try:
        logger.debug(f"Sending HTTP GET request for coordinates ({lat}, {lon})...")
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            logger.debug(f"HTTP request successful for coordinates ({lat}, {lon}).")
            return data
    except urllib.error.HTTPError as http_err:
        logger.error(f"HTTP error fetching weather for ({lat}, {lon}): Status {http_err.code} - {http_err.reason}", exc_info=True)
        raise
    except urllib.error.URLError as url_err:
        logger.error(f"Network/URL error fetching weather for ({lat}, {lon}): {url_err.reason}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_weather_raw for ({lat}, {lon}): {str(e)}", exc_info=True)
        raise


def transform_city_weather(city_config: dict, city_data: dict) -> dict:
    # Transform raw API payload into a structured record matching weather_api.py schema
    city_name = city_config.get("name", "Unknown")
    try:
        logger.debug(f"Starting data transformation for city '{city_name}'...")
        
        # Timezone offset in seconds and hours
        tz_offset = city_data.get("timezone", 0)
        city_uct_offset = tz_offset / 3600.0

        # Extract primary data blocks safely
        main_data = city_data.get("main", {})
        weather_list = city_data.get("weather", [{}])
        weather_first = weather_list[0] if weather_list else {}
        sys_data = city_data.get("sys", {})
        wind_data = city_data.get("wind", {})
        clouds_data = city_data.get("clouds", {})

        # Extract timestamps
        dt = city_data.get("dt", 0)
        sunrise = sys_data.get("sunrise", 0)
        sunset = sys_data.get("sunset", 0)

        transformed_record = {
            "city_name": city_config["name"],
            "city_id": city_data.get("id"),
            "city_uct_offset": city_uct_offset,
            "city_lat": city_data.get("coord", {}).get("lat", city_config["lat"]),
            "city_lon": city_data.get("coord", {}).get("lon", city_config["lon"]),
            "weather": weather_first.get("main"),
            "weather_decr": weather_first.get("description"),
            "weather_icon": weather_first.get("icon"),
            "temp_c": main_data.get("temp"),
            "temp_feels_like_c": main_data.get("feels_like"),
            "temp_min_c": main_data.get("temp_min"),
            "temp_max_c": main_data.get("temp_max"),
            "humidity": main_data.get("humidity"),
            "pressure": main_data.get("pressure"),
            "pressure_sea_lvl": main_data.get("sea_level"),
            "pressure_grnd_lvl": main_data.get("grnd_level"),
            "visibility": city_data.get("visibility"),
            "wind_speed": wind_data.get("speed"),
            "wind_deg": wind_data.get("deg"),
            "clouds": clouds_data.get("all"),
            "sunrise_dt_local": datetime.fromtimestamp(sunrise + tz_offset, tz=timezone.utc).isoformat() if sunrise else None,
            "sunset_dt_local": datetime.fromtimestamp(sunset + tz_offset, tz=timezone.utc).isoformat() if sunset else None,
            "measure_dt_local": datetime.fromtimestamp(dt + tz_offset, tz=timezone.utc).isoformat() if dt else None,
            "measure_dt_utc": datetime.fromtimestamp(dt, tz=timezone.utc).isoformat() if dt else None
        }
        
        logger.debug(f"Data transformation completed successfully for '{city_name}'.")
        return transformed_record

    except Exception as e:
        logger.error(f"Error transforming weather data for city '{city_name}': {str(e)}", exc_info=True)
        raise


def lambda_handler(event, context):
    logger.info("Starting Weather ETL execution...")
    
    # Environment variable verification
    if not API_KEY or not BUCKET_NAME:
        logger.critical("Missing required environment variables: OPENWEATHER_API_KEY or S3_BUCKET_NAME.")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Lambda configuration error: Missing environment variables."})
        }

    all_weather_data = []

    for city in CITIES:
        city_name = city["name"]
        try:
            logger.info(f"Processing city: {city_name} (Lat: {city['lat']}, Lon: {city['lon']})...")
            
            raw_json = get_weather_raw(city["lat"], city["lon"])
            city_weather = transform_city_weather(city, raw_json)
            
            all_weather_data.append(city_weather)
            logger.info(f"Successfully fetched and transformed weather for '{city_name}'.")
            
        except Exception as e:
            logger.warning(f"Skipping '{city_name}' due to an error during execution: {str(e)}")

    if not all_weather_data:
        logger.error("Failed to collect weather data for all configured cities. Aborting S3 upload.")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Failed to collect weather data for any city."})
        }

    try:
        now_utc = datetime.now(timezone.utc)
        s3_key = (
            f"weather/"
            f"year={now_utc.year}/"
            f"month={now_utc.strftime('%m')}/"
            f"day={now_utc.strftime('%d')}/"
            f"weather_{now_utc.strftime('%H%M%S')}.json"
        )

        logger.info(f"Uploading dataset with {len(all_weather_data)} city records to S3 bucket '{BUCKET_NAME}' at key '{s3_key}'...")
        
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_key,
            Body=json.dumps(all_weather_data, ensure_ascii=False, indent=2),
            ContentType='application/json'
        )

        logger.info(f"ETL execution completed successfully. File written to s3://{BUCKET_NAME}/{s3_key}")

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": f"Successfully processed {len(all_weather_data)} cities.",
                "s3_path": f"s3://{BUCKET_NAME}/{s3_key}"
            })
        }
        
    except Exception as e:
        logger.error(f"Error uploading weather data payload to S3 bucket '{BUCKET_NAME}': {str(e)}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"Failed to upload data to S3: {str(e)}"})
        }