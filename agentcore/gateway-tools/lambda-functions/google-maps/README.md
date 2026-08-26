# Google Maps Lambda Function

This Lambda function provides Google Maps Platform APIs through AgentCore Gateway.

## Features

Implements 6 tools using Google Maps Platform APIs:

### 1. search_places
Text-based place search (e.g., "restaurants in Seoul")
- **API**: Places API Text Search
- **Cost**: $32/1000 requests

### 2. search_nearby_places
Search places near a specific location
- **API**: Places API Nearby Search
- **Cost**: $32/1000 requests

### 3. get_place_details
Get detailed information about a place including reviews
- **API**: Places API Place Details
- **Cost**: $17/1000 requests

### 4. get_directions
Get directions between two locations with step-by-step instructions
- **API**: Directions API
- **Cost**: $5/1000 requests

### 5. geocode_address
Convert address to geographic coordinates
- **API**: Geocoding API
- **Cost**: $5/1000 requests

### 6. reverse_geocode
Convert geographic coordinates to address
- **API**: Reverse Geocoding API
- **Cost**: $5/1000 requests

## Setup

### 1. Google Cloud Console Setup

1. Create a project in [Google Cloud Console](https://console.cloud.google.com/)
2. Enable the following APIs:
   - Places API
   - Directions API
   - Geocoding API
3. Create an API key in "APIs & Services" > "Credentials"
4. Restrict the API key:
   - API restrictions: Select only the 3 APIs above
   - Application restrictions: Set HTTP referrer or IP address restrictions

### 2. Store API Key in AWS Secrets Manager

```bash
aws secretsmanager create-secret \
  --name google-maps-api-key \
  --description "Google Maps Platform API Key" \
  --secret-string '{"api_key":"YOUR_API_KEY_HERE"}'
```

### 3. Environment Variables

Set in Lambda configuration:
- `GOOGLE_MAPS_CREDENTIALS_SECRET_NAME`: Name of the secret in Secrets Manager (e.g., `google-maps-api-key`)

Or for local testing:
- `GOOGLE_MAPS_API_KEY`: API key directly

## Tool Definitions

### search_places
```json
{
  "name": "search_places",
  "description": "Search for places using text query (e.g., 'restaurants in Seoul', 'hotels near Gangnam')",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "Search query (e.g., 'Italian restaurant in Hongdae')"
      },
      "location": {
        "type": "string",
        "description": "Optional: Center location as 'lat,lng' to bias results"
      },
      "radius": {
        "type": "integer",
        "description": "Optional: Search radius in meters (max 50000)"
      },
      "type": {
        "type": "string",
        "description": "Optional: Place type (e.g., 'restaurant', 'tourist_attraction', 'lodging')"
      },
      "open_now": {
        "type": "boolean",
        "description": "Optional: Only return places that are open now"
      },
      "language": {
        "type": "string",
        "description": "Optional: Language code (default: 'en')"
      }
    },
    "required": ["query"]
  }
}
```

### search_nearby_places
```json
{
  "name": "search_nearby_places",
  "description": "Search for places near a specific location",
  "inputSchema": {
    "type": "object",
    "properties": {
      "location": {
        "type": "string",
        "description": "Center location as 'lat,lng' (e.g., '37.5665,126.9780')"
      },
      "radius": {
        "type": "integer",
        "description": "Search radius in meters (max 50000)"
      },
      "keyword": {
        "type": "string",
        "description": "Optional: Search keyword"
      },
      "type": {
        "type": "string",
        "description": "Optional: Place type (e.g., 'cafe', 'restaurant')"
      },
      "rank_by": {
        "type": "string",
        "description": "Optional: 'prominence' (default) or 'distance'"
      },
      "language": {
        "type": "string",
        "description": "Optional: Language code (default: 'en')"
      }
    },
    "required": ["location"]
  }
}
```

### get_place_details
```json
{
  "name": "get_place_details",
  "description": "Get detailed information about a place including reviews, phone number, website, opening hours",
  "inputSchema": {
    "type": "object",
    "properties": {
      "place_id": {
        "type": "string",
        "description": "Place ID from search results"
      },
      "language": {
        "type": "string",
        "description": "Optional: Language code (default: 'en')"
      }
    },
    "required": ["place_id"]
  }
}
```

### get_directions
```json
{
  "name": "get_directions",
  "description": "Get directions between two locations with step-by-step instructions",
  "inputSchema": {
    "type": "object",
    "properties": {
      "origin": {
        "type": "string",
        "description": "Starting point (address or 'lat,lng')"
      },
      "destination": {
        "type": "string",
        "description": "Destination (address or 'lat,lng')"
      },
      "mode": {
        "type": "string",
        "description": "Optional: 'driving' (default), 'walking', 'bicycling', 'transit'"
      },
      "alternatives": {
        "type": "boolean",
        "description": "Optional: Return alternative routes"
      },
      "avoid": {
        "type": "string",
        "description": "Optional: 'tolls', 'highways', 'ferries'"
      },
      "language": {
        "type": "string",
        "description": "Optional: Language code (default: 'en')"
      }
    },
    "required": ["origin", "destination"]
  }
}
```

### geocode_address
```json
{
  "name": "geocode_address",
  "description": "Convert address to geographic coordinates",
  "inputSchema": {
    "type": "object",
    "properties": {
      "address": {
        "type": "string",
        "description": "Address to geocode (e.g., '서울시 강남구 테헤란로 152')"
      },
      "language": {
        "type": "string",
        "description": "Optional: Language code (default: 'en')"
      },
      "region": {
        "type": "string",
        "description": "Optional: Country code for bias (default: 'kr')"
      }
    },
    "required": ["address"]
  }
}
```

### reverse_geocode
```json
{
  "name": "reverse_geocode",
  "description": "Convert geographic coordinates to address",
  "inputSchema": {
    "type": "object",
    "properties": {
      "latlng": {
        "type": "string",
        "description": "Coordinates as 'lat,lng' (e.g., '37.5665,126.9780')"
      },
      "language": {
        "type": "string",
        "description": "Optional: Language code (default: 'en')"
      }
    },
    "required": ["latlng"]
  }
}
```

## Usage Examples

### Search Places
```python
{
  "query": "서울 강남역 레스토랑",
  "radius": 1000,
  "type": "restaurant",
  "open_now": true
}
```

### Get Place Details
```python
{
  "place_id": "ChIJN1t_tDeuEmsRUsoyG83frY4",
  "language": "ko"
}
```

### Get Directions
```python
{
  "origin": "서울역",
  "destination": "강남역",
  "mode": "transit",
  "language": "ko"
}
```

## Cost Estimation

For 100 users per day:
- 500 place searches × $32/1000 = $16/month
- 200 place details × $17/1000 = $3.4/month
- 100 directions × $5/1000 = $0.5/month
- 100 geocoding × $5/1000 = $0.5/month

**Total: ~$20/month** (well within $200 free credit)

## Free Quota

Google Maps Platform provides:
- **$200/month free credit** (covers ~10,000 place searches or ~40,000 geocoding requests)
- Places Text/Nearby Search: Free up to quota
- Place Details: Free up to quota
- Directions: Free up to quota
- Geocoding: Free up to quota

## Deployment

This Lambda is deployed via Terraform (`infra/modules/gateway-lambda-tool`):

```bash
./infra/scripts/deploy.sh apply
```
