"""
Google Maps Lambda for AgentCore Gateway
Provides Places API, Directions API, and Geocoding API tools
"""
import json  # noqa: E402
import os  # noqa: E402
import logging  # noqa: E402
from typing import Dict, Any, Optional  # noqa: E402

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Import after logger setup
import googlemaps  # noqa: E402
import boto3  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402

# Cache for API credentials
_credentials_cache: Optional[str] = None
_gmaps_client: Optional[googlemaps.Client] = None


def lambda_handler(event, context):
    """
    Lambda handler for Google Maps tools via AgentCore Gateway

    Gateway unwraps tool arguments and passes them directly to Lambda
    """
    try:
        logger.info(f"Event: {json.dumps(event)}")

        # Get tool name from context (set by AgentCore Gateway)
        tool_name = 'unknown'
        if hasattr(context, 'client_context') and context.client_context:
            if hasattr(context.client_context, 'custom'):
                tool_name = context.client_context.custom.get('bedrockAgentCoreToolName', '')
                if '___' in tool_name:
                    tool_name = tool_name.split('___')[-1]

        logger.info(f"Tool name: {tool_name}")

        # Route to appropriate tool
        if tool_name == 'search_places':
            return search_places(event)
        elif tool_name == 'search_nearby_places':
            return search_nearby_places(event)
        elif tool_name == 'get_place_details':
            return get_place_details(event)
        elif tool_name == 'get_directions':
            return get_directions(event)
        elif tool_name == 'geocode_address':
            return geocode_address(event)
        elif tool_name == 'reverse_geocode':
            return reverse_geocode(event)
        elif tool_name == 'show_on_map':
            return show_on_map(event)
        else:
            return error_response(f"Unknown tool: {tool_name}")

    except Exception as e:
        logger.error(f"Error: {str(e)}", exc_info=True)
        return error_response(str(e))


def get_google_maps_client() -> Optional[googlemaps.Client]:
    """Get Google Maps client with API key from Secrets Manager (with caching)."""
    global _credentials_cache, _gmaps_client

    if _gmaps_client:
        return _gmaps_client

    # Check environment variable first (for local testing)
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")

    if api_key:
        _credentials_cache = api_key
        _gmaps_client = googlemaps.Client(key=api_key)
        return _gmaps_client

    # Get from Secrets Manager
    secret_name = os.getenv("GOOGLE_MAPS_CREDENTIALS_SECRET_NAME")
    if not secret_name:
        logger.error("GOOGLE_MAPS_CREDENTIALS_SECRET_NAME not set")
        return None

    try:
        session = boto3.session.Session()
        client = session.client(service_name='secretsmanager')

        get_secret_value_response = client.get_secret_value(SecretId=secret_name)

        # Parse secret (stored as JSON with 'api_key' field)
        secret_str = get_secret_value_response['SecretString']
        credentials = json.loads(secret_str)
        api_key = credentials.get('api_key')

        if not api_key:
            logger.error("api_key not found in secret")
            return None

        # Cache for future calls
        _credentials_cache = api_key
        _gmaps_client = googlemaps.Client(key=api_key)
        logger.info("✅ Google Maps client initialized from Secrets Manager")

        return _gmaps_client

    except ClientError as e:
        logger.error(f"Failed to get Google Maps credentials from Secrets Manager: {e}")
        return None




def search_places(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Text-based place search (e.g., "restaurants in Seoul")

    Uses Places API Text Search
    """
    gmaps = get_google_maps_client()
    if not gmaps:
        return error_response("Failed to initialize Google Maps client")

    # Extract parameters
    query = params.get('query')
    location = params.get('location')  # Optional: "lat,lng"
    radius = params.get('radius')      # Optional: meters
    place_type = params.get('type')    # Optional: "restaurant", "tourist_attraction", etc.
    min_price = params.get('min_price')
    max_price = params.get('max_price')
    open_now = params.get('open_now', False)
    language = params.get('language', 'en')

    if not query:
        return error_response("query parameter required")

    logger.info(f"Searching places: query={query}, location={location}, radius={radius}")

    try:
        # Call Places API
        result = gmaps.places(
            query=query,
            location=location,
            radius=radius,
            language=language,
            min_price=min_price,
            max_price=max_price,
            open_now=open_now,
            type=place_type
        )

        # Format results for LLM
        places = []
        for idx, place in enumerate(result.get('results', [])[:10], 1):
            places.append({
                "index": idx,
                "name": place.get('name', 'Unknown'),
                "place_id": place.get('place_id'),
                "address": place.get('formatted_address', 'No address'),
                "location": {
                    "lat": place.get('geometry', {}).get('location', {}).get('lat'),
                    "lng": place.get('geometry', {}).get('location', {}).get('lng')
                },
                "rating": place.get('rating'),
                "user_ratings_total": place.get('user_ratings_total'),
                "price_level": place.get('price_level'),
                "types": place.get('types', []),
                "opening_hours": {
                    "open_now": place.get('opening_hours', {}).get('open_now')
                } if 'opening_hours' in place else None,
                "maps_url": f"https://www.google.com/maps/place/?q=place_id:{place.get('place_id')}"
            })

        result_data = {
            "query": query,
            "results_count": len(places),
            "places": places
        }

        return success_response(json.dumps(result_data, indent=2, ensure_ascii=False))

    except Exception as e:
        logger.error(f"Places search error: {str(e)}", exc_info=True)
        return error_response(f"Places search error: {str(e)}")


def search_nearby_places(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Search places near a specific location

    Uses Places API Nearby Search
    """
    gmaps = get_google_maps_client()
    if not gmaps:
        return error_response("Failed to initialize Google Maps client")

    # Extract parameters
    location = params.get('location')  # Required: "lat,lng"
    radius = params.get('radius')      # Required: meters (max 50000)
    keyword = params.get('keyword')    # Optional: search keyword
    place_type = params.get('type')    # Optional: "restaurant", "cafe", etc.
    rank_by = params.get('rank_by', 'prominence')  # "prominence" or "distance"
    min_price = params.get('min_price')
    max_price = params.get('max_price')
    open_now = params.get('open_now', False)
    language = params.get('language', 'en')

    if not location:
        return error_response("location parameter required (format: 'lat,lng')")

    logger.info(f"Searching nearby places: location={location}, radius={radius}, keyword={keyword}")

    try:
        # Call Places API Nearby Search
        result = gmaps.places_nearby(
            location=location,
            radius=radius if rank_by != 'distance' else None,
            keyword=keyword,
            language=language,
            min_price=min_price,
            max_price=max_price,
            open_now=open_now,
            rank_by=rank_by,
            type=place_type
        )

        # Format results
        places = []
        for idx, place in enumerate(result.get('results', [])[:10], 1):
            places.append({
                "index": idx,
                "name": place.get('name', 'Unknown'),
                "place_id": place.get('place_id'),
                "vicinity": place.get('vicinity', 'No address'),
                "location": {
                    "lat": place.get('geometry', {}).get('location', {}).get('lat'),
                    "lng": place.get('geometry', {}).get('location', {}).get('lng')
                },
                "rating": place.get('rating'),
                "user_ratings_total": place.get('user_ratings_total'),
                "price_level": place.get('price_level'),
                "types": place.get('types', []),
                "opening_hours": {
                    "open_now": place.get('opening_hours', {}).get('open_now')
                } if 'opening_hours' in place else None,
                "maps_url": f"https://www.google.com/maps/place/?q=place_id:{place.get('place_id')}"
            })

        result_data = {
            "location": location,
            "radius": radius,
            "keyword": keyword,
            "results_count": len(places),
            "places": places
        }

        return success_response(json.dumps(result_data, indent=2, ensure_ascii=False))

    except Exception as e:
        logger.error(f"Nearby search error: {str(e)}", exc_info=True)
        return error_response(f"Nearby search error: {str(e)}")


def get_place_details(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get detailed information about a place including reviews

    Uses Places API Place Details
    """
    gmaps = get_google_maps_client()
    if not gmaps:
        return error_response("Failed to initialize Google Maps client")

    # Extract parameters
    place_id = params.get('place_id')
    language = params.get('language', 'en')

    if not place_id:
        return error_response("place_id parameter required")

    logger.info(f"Getting place details: place_id={place_id}")

    try:
        # Request specific fields to optimize cost
        # googlemaps uses the legacy Places field identifiers here: the request
        # names are singular even though the response keys are plural.
        fields = [
            "name", "formatted_address", "formatted_phone_number",
            "geometry", "rating", "user_ratings_total",
            "review", "photo", "type", "website", "opening_hours",
            "price_level", "url"
        ]

        # Call Places API Place Details
        result = gmaps.place(
            place_id=place_id,
            fields=fields,
            language=language
        )

        place = result.get('result', {})

        # Format detailed place info
        place_details = {
            "place_id": place_id,
            "name": place.get('name', 'Unknown'),
            "formatted_address": place.get('formatted_address'),
            "formatted_phone_number": place.get('formatted_phone_number'),
            "website": place.get('website'),
            "location": {
                "lat": place.get('geometry', {}).get('location', {}).get('lat'),
                "lng": place.get('geometry', {}).get('location', {}).get('lng')
            },
            "rating": place.get('rating'),
            "user_ratings_total": place.get('user_ratings_total'),
            "price_level": place.get('price_level'),
            "types": place.get('types', []),
            "opening_hours": {
                "open_now": place.get('opening_hours', {}).get('open_now'),
                "weekday_text": place.get('opening_hours', {}).get('weekday_text')
            } if 'opening_hours' in place else None,
            "reviews": [],
            "photos_count": len(place.get('photos', [])),
            "google_maps_url": place.get('url'),
            "maps_url": f"https://www.google.com/maps/place/?q=place_id:{place_id}"
        }

        # Add reviews (up to 5)
        for review in place.get('reviews', [])[:5]:
            place_details['reviews'].append({
                "author_name": review.get('author_name'),
                "rating": review.get('rating'),
                "text": review.get('text'),
                "time": review.get('time'),
                "relative_time_description": review.get('relative_time_description')
            })

        return success_response(json.dumps(place_details, indent=2, ensure_ascii=False))

    except Exception as e:
        logger.error(f"Place details error: {str(e)}", exc_info=True)
        return error_response(f"Place details error: {str(e)}")


def get_directions(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get directions between two locations

    Uses Directions API
    """
    gmaps = get_google_maps_client()
    if not gmaps:
        return error_response("Failed to initialize Google Maps client")

    # Extract parameters
    origin = params.get('origin')          # Required: address or "lat,lng"
    destination = params.get('destination')  # Required: address or "lat,lng"
    mode = params.get('mode', 'driving')   # "driving", "walking", "bicycling", "transit"
    alternatives = params.get('alternatives', False)
    avoid = params.get('avoid')            # "tolls", "highways", "ferries"
    language = params.get('language', 'en')
    departure_time = params.get('departure_time')  # "now" or timestamp

    if not origin or not destination:
        return error_response("origin and destination parameters required")

    logger.info(f"Getting directions: {origin} -> {destination}, mode={mode}")

    try:
        # Call Directions API
        result = gmaps.directions(
            origin=origin,
            destination=destination,
            mode=mode,
            alternatives=alternatives,
            avoid=avoid,
            language=language,
            units='metric',
            departure_time=departure_time if departure_time == 'now' else None
        )

        if not result:
            return error_response("No routes found")

        # Format routes
        routes = []
        for route in result:
            leg = route['legs'][0]  # Single leg for simple A->B

            # Extract steps
            steps = []
            for step in leg.get('steps', []):
                steps.append({
                    "instructions": step.get('html_instructions', '').replace('<b>', '').replace('</b>', ''),
                    "distance": step.get('distance', {}).get('text'),
                    "duration": step.get('duration', {}).get('text'),
                    "travel_mode": step.get('travel_mode')
                })

            routes.append({
                "summary": route.get('summary'),
                "distance": leg.get('distance', {}).get('text'),
                "duration": leg.get('duration', {}).get('text'),
                "start_address": leg.get('start_address'),
                "end_address": leg.get('end_address'),
                "start_location": leg.get('start_location'),
                "end_location": leg.get('end_location'),
                "steps": steps[:15]  # Limit to first 15 steps for brevity
            })

        result_data = {
            "origin": origin,
            "destination": destination,
            "mode": mode,
            "routes_count": len(routes),
            "routes": routes
        }

        return success_response(json.dumps(result_data, indent=2, ensure_ascii=False))

    except Exception as e:
        logger.error(f"Directions error: {str(e)}", exc_info=True)
        return error_response(f"Directions error: {str(e)}")


def geocode_address(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert address to geographic coordinates

    Uses Geocoding API
    """
    gmaps = get_google_maps_client()
    if not gmaps:
        return error_response("Failed to initialize Google Maps client")

    # Extract parameters
    address = params.get('address')
    language = params.get('language', 'en')
    region = params.get('region')  # Optional country bias

    if not address:
        return error_response("address parameter required")

    logger.info(f"Geocoding address: {address}")

    try:
        # Call Geocoding API
        result = gmaps.geocode(
            address=address,
            language=language,
            region=region
        )

        if not result:
            return error_response("Address not found")

        # Format results
        locations = []
        for item in result[:5]:  # Limit to top 5 results
            locations.append({
                "formatted_address": item.get('formatted_address'),
                "place_id": item.get('place_id'),
                "location": {
                    "lat": item.get('geometry', {}).get('location', {}).get('lat'),
                    "lng": item.get('geometry', {}).get('location', {}).get('lng')
                },
                "location_type": item.get('geometry', {}).get('location_type'),
                "types": item.get('types', []),
                "maps_url": f"https://www.google.com/maps/place/?q=place_id:{item.get('place_id')}"
            })

        result_data = {
            "address": address,
            "results_count": len(locations),
            "locations": locations
        }

        return success_response(json.dumps(result_data, indent=2, ensure_ascii=False))

    except Exception as e:
        logger.error(f"Geocoding error: {str(e)}", exc_info=True)
        return error_response(f"Geocoding error: {str(e)}")


def reverse_geocode(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert geographic coordinates to address

    Uses Reverse Geocoding API
    """
    gmaps = get_google_maps_client()
    if not gmaps:
        return error_response("Failed to initialize Google Maps client")

    # Extract parameters
    latlng = params.get('latlng')  # Required: "lat,lng"
    language = params.get('language', 'en')

    if not latlng:
        return error_response("latlng parameter required (format: 'lat,lng')")

    logger.info(f"Reverse geocoding: {latlng}")

    try:
        # Call Reverse Geocoding API
        result = gmaps.reverse_geocode(
            latlng=latlng,
            language=language
        )

        if not result:
            return error_response("No address found for coordinates")

        # Format results
        addresses = []
        for item in result[:5]:  # Limit to top 5 results
            addresses.append({
                "formatted_address": item.get('formatted_address'),
                "place_id": item.get('place_id'),
                "types": item.get('types', []),
                "maps_url": f"https://www.google.com/maps/place/?q=place_id:{item.get('place_id')}"
            })

        result_data = {
            "latlng": latlng,
            "results_count": len(addresses),
            "addresses": addresses
        }

        return success_response(json.dumps(result_data, indent=2, ensure_ascii=False))

    except Exception as e:
        logger.error(f"Reverse geocoding error: {str(e)}", exc_info=True)
        return error_response(f"Reverse geocoding error: {str(e)}")


def success_response(content: str) -> Dict[str, Any]:
    """Format successful MCP response"""
    return {
        'statusCode': 200,
        'body': json.dumps({
            'content': [{
                'type': 'text',
                'text': content
            }]
        })
    }


def error_response(message: str) -> Dict[str, Any]:
    """Format error response"""
    logger.error(f"Error response: {message}")
    return {
        'statusCode': 400,
        'body': json.dumps({
            'error': message
        })
    }


# ============================================================
# Map Visualization Tool (show_on_map)
# ============================================================

def validate_marker(marker: dict) -> tuple:
    """Validate marker structure"""
    if "lat" not in marker or "lng" not in marker:
        return False, "Marker must have 'lat' and 'lng' fields"

    lat = marker.get("lat")
    lng = marker.get("lng")

    if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
        return False, "Latitude and longitude must be numbers"

    if not (-90 <= lat <= 90):
        return False, f"Latitude {lat} out of range (-90 to 90)"

    if not (-180 <= lng <= 180):
        return False, f"Longitude {lng} out of range (-180 to 180)"

    return True, None


def validate_directions(directions: dict) -> tuple:
    """Validate directions structure"""
    if "origin" not in directions or "destination" not in directions:
        return False, "Directions must have 'origin' and 'destination' fields"

    origin = directions.get("origin")
    if not isinstance(origin, dict) or "lat" not in origin or "lng" not in origin:
        return False, "Origin must be a dict with 'lat' and 'lng'"

    destination = directions.get("destination")
    if not isinstance(destination, dict) or "lat" not in destination or "lng" not in destination:
        return False, "Destination must be a dict with 'lat' and 'lng'"

    return True, None


def calculate_center_from_markers(markers: list) -> dict:
    """Calculate center point from list of markers"""
    if not markers:
        return {"lat": 0, "lng": 0}

    lat_sum = sum(m["lat"] for m in markers)
    lng_sum = sum(m["lng"] for m in markers)

    return {
        "lat": lat_sum / len(markers),
        "lng": lng_sum / len(markers)
    }


def calculate_zoom_level(markers: Optional[list], directions: Optional[dict]) -> int:
    """
    Calculate appropriate zoom level based on data spread

    Zoom levels (approximate):
    - 1: World
    - 5: Continent
    - 10: City
    - 15: Streets
    - 20: Buildings
    """
    if not markers and not directions:
        return 12  # Default

    coords = []

    # Collect all coordinates
    if markers:
        coords.extend([(m["lat"], m["lng"]) for m in markers])

    if directions:
        origin = directions.get("origin", {})
        dest = directions.get("destination", {})
        if "lat" in origin and "lng" in origin:
            coords.append((origin["lat"], origin["lng"]))
        if "lat" in dest and "lng" in dest:
            coords.append((dest["lat"], dest["lng"]))

    if len(coords) <= 1:
        return 15  # Single point - street level

    # Calculate lat/lng ranges
    lats = [c[0] for c in coords]
    lngs = [c[1] for c in coords]

    lat_range = max(lats) - min(lats)
    lng_range = max(lngs) - min(lngs)
    max_range = max(lat_range, lng_range)

    # Estimate zoom level based on coordinate range
    if max_range > 10:
        return 5   # Continental
    elif max_range > 1:
        return 9   # Regional
    elif max_range > 0.1:
        return 12  # City
    elif max_range > 0.01:
        return 14  # Neighborhood
    else:
        return 15  # Street


def show_on_map(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Display locations, routes, or areas on an interactive Google Map

    Args:
        map_type: "markers" (location pins), "directions" (route), or "area" (region)
        markers: List of location markers for map_type="markers"
        directions: Route data for map_type="directions"
        center: Map center {lat, lng}. Auto-calculated if omitted.
        zoom: Zoom level 1-20. Auto-calculated if omitted.

    Returns:
        Map visualization data for frontend rendering
    """
    try:
        map_type = event.get("map_type")
        markers = event.get("markers")
        directions = event.get("directions")
        center = event.get("center")
        zoom = event.get("zoom")

        # Validate map_type
        if map_type not in ["markers", "directions", "area"]:
            return error_response(f"Invalid map_type: {map_type}. Must be 'markers', 'directions', or 'area'")

        # Validate required data based on type
        if map_type == "markers":
            if not markers or len(markers) == 0:
                return error_response("markers parameter required and must not be empty for map_type='markers'")

            # Validate each marker
            for idx, marker in enumerate(markers):
                is_valid, error_msg = validate_marker(marker)
                if not is_valid:
                    return error_response(f"Marker {idx}: {error_msg}")

        elif map_type == "directions":
            if not directions:
                return error_response("directions parameter required for map_type='directions'")

            is_valid, error_msg = validate_directions(directions)
            if not is_valid:
                return error_response(f"Directions: {error_msg}")

        # Auto-calculate center if not provided
        if not center:
            if map_type == "markers" and markers:
                center = calculate_center_from_markers(markers)
            elif map_type == "directions" and directions:
                # Center between origin and destination
                origin = directions.get("origin", {})
                dest = directions.get("destination", {})
                center = {
                    "lat": (origin.get("lat", 0) + dest.get("lat", 0)) / 2,
                    "lng": (origin.get("lng", 0) + dest.get("lng", 0)) / 2
                }
            else:
                center = {"lat": 0, "lng": 0}

        # Auto-calculate zoom if not provided
        if not zoom:
            zoom = calculate_zoom_level(markers, directions)

        # Ensure zoom is in valid range
        zoom = max(1, min(20, zoom))

        # Build map data structure
        map_data = {
            "type": map_type,
            "center": center,
            "zoom": zoom
        }

        if markers:
            map_data["markers"] = markers

        if directions:
            map_data["directions"] = directions

        # Create success message
        if map_type == "markers":
            count = len(markers) if markers else 0
            message = f"📍 Showing {count} location(s) on map"
        elif map_type == "directions":
            origin_addr = directions.get("origin", {}).get("address", "Start")
            dest_addr = directions.get("destination", {}).get("address", "End")
            message = f"🗺️ Showing route from {origin_addr} to {dest_addr}"
        else:
            message = f"🗺️ Showing map ({map_type})"

        logger.info(f"Created map visualization: type={map_type}, markers={len(markers or [])}, zoom={zoom}")

        # Return success response
        result_data = {
            "success": True,
            "map_data": map_data,
            "message": message
        }
        return success_response(json.dumps(result_data))

    except Exception as e:
        logger.error(f"Error in show_on_map: {e}", exc_info=True)
        return error_response(str(e))
