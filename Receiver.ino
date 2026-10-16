// Receiver: ESP32 DevKit V1 + Lora module -> ThingSpeak

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>

// Wifi Setup
const char* WIFI_NAME     = "Redmi 14C";
const char* WIFI_PASSWORD = "20062008a";
const char* TS_WRITE_KEY  = "LEI66Y2ZT1QJGLKA";

#define LORA_RX 16 // ESP32 receives here
#define LORA_TX 4 // ESP32 transmits here

// Time interval
const unsigned long UPLOAD_INTERVAL = 16000;

const String THINGSPEAK_URL = "https://api.thingspeak.com/update?api_key=LEI66Y2ZT1QJGLKA";

unsigned long lastThingSpeakUpdate = 0;
bool hasUploaded = false;

void setup() {
  // put your setup code here, to run once:
  Serial.begin(115200);                                
  Serial2.begin(9600, SERIAL_8N1, LORA_RX, LORA_TX);
  Serial2.setTimeout(200);
  connectWiFi();
  Serial.println("ESP32 receiver ready");
}

void loop() {
  // put your main code here, to run repeatedly:
  if (Serial2.available()) {
    String line = Serial2.readStringUntil('\n');
    line.trim();
    if (line.length() > 0) {
      handleMessage(line);
    }
  }
}

void connectWiFi() {
  WiFi.begin(WIFI_NAME, WIFI_PASSWORD);
  Serial.println("Connecting to WiFi");
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(500);
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("\nConnected, IP address: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\nWiFi connection failed (will retry when data arrives)");
  }
}

void handleMessage(const String& line) {
  Serial.print("Received: ");
  Serial.println(line);

  float temperature, humidity;
  if (sscanf(line.c_str(), "T:%f,H:%f", &temperature, &humidity) != 2) {
    Serial.println("Ignored: message format not recognised");
    return;
  }

  if (temperature < -20 || temperature > 60 || humidity < 0 || humidity > 100) {
    Serial.println("Ignored: values out of range");
    return;
  }

  if (hasUploaded && millis() - lastThingSpeakUpdate < UPLOAD_INTERVAL) {
    Serial.println("Skipped: too soon after the last upload (ThingSpeak limit)");
    return;
  }

  uploadToThingSpeak(temperature, humidity);
}

void uploadToThingSpeak(float temperature, float humidity) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi is down, reconnecting...");
    connectWiFi();
    if (WiFi.status() != WL_CONNECTED) {
      return;
    }
  }

  char para[120];
  sprintf(para, "&field1=%.2f&field2=%.2f", temperature, humidity);
  String Url = THINGSPEAK_URL + String(para);

  // Upload results on ThingSpeak
  if ((unsigned long) (millis() - lastThingSpeakUpdate) > UPLOAD_INTERVAL)
  {
    httpGETRequest(Url);
    lastThingSpeakUpdate = millis();
    hasUploaded = true;
  }
}


// ThingSpeak HTTP Request
String httpGETRequest(String Url) {
  HTTPClient http;
  Serial.println(Url);
  http.begin(Url);
  int responseCode = http.GET();
  String responseBody = "{}";

  if (responseCode > 0) {
    Serial.print("responseCode:");
    Serial.println(responseCode);
    responseBody = http.getString();
  } else {
    Serial.print("Error Code: ");
    Serial.println(responseCode);
  }

  http.end();
  return responseBody;
}



