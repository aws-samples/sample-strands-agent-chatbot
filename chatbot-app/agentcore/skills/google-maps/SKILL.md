---
name: google-maps
description: Place search, directions, geocoding, and interactive maps
---

# Google Maps

## Available Tools

- **search_places(query, location?, radius?, type?, open_now?, language?)**: Search for places using text query.
  - `query` (string, required): Search text (e.g., "restaurants in Seoul")
  - `location` (string, optional): Center location as "lat,lng" (e.g., "37.5665,126.9780")
  - `radius` (integer, optional): Search radius in meters (max 50,000)
  - `type` (string, optional): Place type filter (e.g., "restaurant", "tourist_attraction", "hotel")
  - `open_now` (boolean, optional, default: false): Only return currently open places
  - `language` (string, optional, default: "en"): Language code

- **search_nearby_places(location, radius, keyword?, type?, rank_by?, open_now?, language?)**: Search near specific coordinates.
  - `location` (string, required): Center as "lat,lng" (e.g., "37.5665,126.9780")
  - `radius` (integer, required): Search radius in meters (max 50,000)
  - `keyword` (string, optional): Search keyword
  - `type` (string, optional): Place type filter
  - `rank_by` (string, optional, default: "prominence"): "prominence" or "distance"
  - `open_now` (boolean, optional, default: false): Only open places
  - `language` (string, optional, default: "en"): Language code

- **get_place_details(place_id, language?)**: Get detailed place info including reviews and hours.
  - `place_id` (string, required): Place ID from search results
  - `language` (string, optional, default: "en"): Language code

- **get_directions(origin, destination, mode?, alternatives?, avoid?, language?)**: Get directions between two locations.
  - `origin` (string, required): Starting point (address or "lat,lng")
  - `destination` (string, required): Destination (address or "lat,lng")
  - `mode` (string, optional, default: "driving"): "driving", "walking", "bicycling", "transit"
  - `alternatives` (boolean, optional, default: false): Return alternative routes
  - `avoid` (string, optional): "tolls", "highways", or "ferries"
  - `language` (string, optional, default: "en"): Language code

- **geocode_address(address, language?, region?)**: Convert a text address to geographic coordinates.
  - `address` (string, required): Address to geocode
  - `language` (string, optional, default: "en"): Language code
  - `region` (string, optional): Country code for regional bias

- **reverse_geocode(latlng, language?)**: Convert coordinates to a human-readable address.
  - `latlng` (string, required): Coordinates as "lat,lng" (e.g., "37.5665,126.9780")
  - `language` (string, optional, default: "en"): Language code

- **show_on_map(map_type, markers?, directions?, center?, zoom?)**: Display locations or routes on an interactive map.
  - `map_type` (string, required): "markers" (location pins), "directions" (route), or "area"
  - `markers` (array, required for "markers"): List of marker objects with lat, lng
  - `directions` (object, required for "directions"): Route data with origin and destination
  - `center` (object, optional): Map center {lat, lng}
  - `zoom` (integer, optional): Zoom level 1-20

## Usage Guidelines

- **ALWAYS call show_on_map** after collecting location data — visual maps are the primary value.
- Preserve `place_id` from search results for use with `get_place_details`.
- Infer transportation mode from context (driving for long distances, walking for short ones).
- Show 1-5 most relevant places per map.

## Response Pattern

Follow the **Text -> Map -> Text** sequence. Do NOT call `show_on_map` in parallel with other tool calls.

1. **Text**: Introduce what you'll show
2. **Map**: Call `show_on_map` to display results
3. **Text**: Explain the results

**Multiple categories**: Use separate maps in sequence — never parallel.

## Output Rules

The map renders visually from the tool result — never echo or paraphrase the raw `map_data` JSON (type, center, zoom, markers, lat/lng) in your text response. Describe places in natural language only (names, ratings, brief notes). The user already sees the map; do not repeat its contents.

## UI Guidance (from tools-config)

**When to Use:**
- ALWAYS call show_on_map after collecting location data - visual maps are the primary value
- Use for: search results, directions, comparisons, or any location-based query

**Response Pattern (DO NOT call show_on_map in parallel):**
1. Text: Introduce what you'll show ("Here are the top restaurants:")
2. show_on_map: Display the map
3. Text: Explain the results ("These restaurants are rated 4.5+ stars")

Example:
- "Let me show you restaurants in Gangnam:" → show_on_map(restaurants) → "All 5 restaurants above have excellent reviews."
- For multiple categories: Text → Map 1 → Text → Map 2 (sequential, not parallel)

**Map Organization:**
- Separate maps for different categories (restaurants vs hotels)
- Show 1-5 most relevant places per map

**Tool Selection:**
- search_places: text queries | search_nearby_places: coordinates
- Preserve place_id for details | Infer direction mode from context

**Output Rules:**
- The map is rendered visually by the UI from the tool result. NEVER echo, paraphrase, or include the raw map_data JSON (coordinates, markers, zoom, type) in your text response.
- Describe locations in natural language only (names, ratings, brief notes). The user already sees the map — do not repeat its contents.
