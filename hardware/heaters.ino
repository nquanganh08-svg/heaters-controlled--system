#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>

// Wifi Setup
const char* WIFI_NAME     = "Redmi 14C";
const char* WIFI_PASSWORD = "20062008a";

// Control channel (the website writes, this board only reads) - server Heater_Channel
//   field 1 = manual command (1 = on, 0 = off)
//   field 2 = mode (0 = manual, 1 = auto)
//   field 3 = low temperature  (heater turns ON below this)
//   field 4 = high temperature (heater turns OFF above this)
const char* CONTROL_CHANNEL_ID = "3540791";
const char* CONTROL_READ_KEY = "2G8V9OSJC4WMG9LN";

// Sensor channel (field 1 = temperature) - server Temperature and Humidity Measuring
const char* SENSOR_CHANNEL_ID = "3471956";
const char* SENSOR_READ_KEY = "K6NYV69QN18DQPC4";

const int LED_PINS[] = {2};
const int NUM_LEDS = sizeof(LED_PINS) / sizeof(LED_PINS[0]);
bool ledState[NUM_LEDS];

// Limits fir the temperature settings (all the temperatures are in degrees C)
const float MIN_SETTING = 5;
const float MAX_SETTING = 40;
const float MIN_GAP = 2; // high must be at least this much above low

// Time intervak
const unsigned long COMMAND_INTERVAL = 20000; 
const unsigned long FAILSAFE_TIME = 300000;
const float MAX_DATA_AGE = 180; // seconds, in auto mode the heater is off if the temperature is older than this

unsigned long lastCheck = 0;
unsigned long lastSuccess = 0; 

void setup() {
  // put your setup code here, to run once:
    Serial.begin(115200);
    for (int i = 0; i < NUM_LEDS; i++) {
      pinMode(LED_PINS[i], OUTPUT);
      digitalWrite(LED_PINS[i], LOW);
  }
  connectWiFi();
  checkHeaterCommand();
  Serial.println("Heater controller ready");
}

void loop() {
  // put your main code here, to run repeatedly:
  if (millis() - lastCheck > COMMAND_INTERVAL)
  {
    lastCheck = millis();
    checkHeaterCommand();
  }
  // In case there is Wifi off/blackout, the heaters will turn off to save energy
  if (checkHeaterOn() && millis() - lastSuccess > FAILSAFE_TIME)
  {
    for (int i = 0; i < NUM_LEDS; i++)
    {
      setHeater(i, false);
    }
    Serial.println("No answer from the server for 5 minutes. Heaters will be off.");
  }

  delay(100);
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
    Serial.println("\nWiFi connection failed (will retry at the next check)");
  }
}

// If the mode = 0, it is a manual mode. If the mode = 1, it is automatic mode
void checkHeaterCommand() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi is down, cannot read the heater command");
    connectWiFi();
    if (WiFi.status() != WL_CONNECTED) {
      return;
    }
  }


  float mode;
  if (!readNumber(readUrl(CONTROL_CHANNEL_ID, CONTROL_READ_KEY, "/fields/2/last.txt"), mode)) {
    Serial.println("Could not read the mode");
    return;
  }
  lastSuccess = millis();

  if (mode == 0){
    manualMode();
  } 
  else if (mode == 1){
    autoMode();
  }
  else {
    Serial.println("Mode is unknown. Heaters will be off");
    for (int i = 0; i < NUM_LEDS; i++)
    {
      setHeater(i, false);
    }
  }
}

// Manual mode: Admins manually control the heaters through websites
void manualMode(){
  float command;
  if (!readNumber(readUrl(CONTROL_CHANNEL_ID, CONTROL_READ_KEY, "/fields/1/last.txt"), command)) {
    Serial.println("Could not read the manual command");
    return;
  }
  for (int i = 0; i < NUM_LEDS; i++) {
    if (command == 1) {
    setHeater(i, true);
    } 
    else if (command == 0) {
    setHeater(i, false);
    } 
    else {
    Serial.println("Unexpected manual command");
    }
  }
}

void autoMode(){
  float low, high, temperature, age;
  for (int i = 0; i < NUM_LEDS; i++){
    if (!readNumber(readUrl(CONTROL_CHANNEL_ID, CONTROL_READ_KEY, "/fields/3/last.txt"), low) ||
      !readNumber(readUrl(CONTROL_CHANNEL_ID, CONTROL_READ_KEY, "/fields/4/last.txt"), high)) {
    Serial.println("Could not read the temperature limits, heater off");
    setHeater(i, false);
    return;
    }

    if (low < MIN_SETTING || high > MAX_SETTING || high - low < MIN_GAP) {
    Serial.println("Temperature limits are not valid, heater off");
    setHeater(i, false);
    return;
    }

    if (!readNumber(readUrl(SENSOR_CHANNEL_ID, SENSOR_READ_KEY, "/fields/1/last.txt"), temperature) ||
      !readNumber(readUrl(SENSOR_CHANNEL_ID, SENSOR_READ_KEY, "/fields/1/last_data_age.txt"), age)) {
    Serial.println("Could not read the temperature, heater off");
    setHeater(i, false);
    return;
    }

    if (age > MAX_DATA_AGE) {
    Serial.println("Temperature data is too old, heater off");
    setHeater(i, false);
    return;
    }

  Serial.print("Auto: temperature ");
  Serial.print(temperature, 1);
  Serial.print(", low ");
  Serial.print(low, 1);
  Serial.print(", high ");
  Serial.println(high, 1);

    if (temperature < low) {
      setHeater(i, true);
    } else if (temperature > high) {
      setHeater(i, false);
    }
  }
}


// ThingSpeak read address
String readUrl(const char* channelId, const char* readKey, String path) {
  String Url = "https://api.thingspeak.com/channels/";
  Url += channelId;
  Url += path;
  Url += "?api_key=";
  Url += readKey;
  return Url;
}

bool readNumber(String Url, float &value) {
  String reply = httpGETRequest(Url);
  reply.trim();
  if (reply.length() == 0 || reply == "-1") {
    return false;
  }
  char* end;
  float number = strtof(reply.c_str(), &end);
  if (*end != '\0') {
    return false;
  }
  value = number;
  return true;
}

// Turn on and off one heater
void setHeater(int i, bool on) {
  if (on) {
    digitalWrite(LED_PINS[i], HIGH);
} else {
    digitalWrite(LED_PINS[i], LOW);
}
  if (on != ledState[i]) {
    ledState[i] = on;
    Serial.print("Heater ");
    Serial.print(i + 1);

    if (on) {
      Serial.println(" ON");
    } else {
      Serial.println(" OFF");
    }
  }
}

bool checkHeaterOn()
{
  for (int i = 0; i < NUM_LEDS; i++)
  {
    if (ledState[i]){
      return true;
    }
  }
  return false;
}

// ThingSpeak HTTP Request
String httpGETRequest(String Url) {
  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;
  http.begin(client, Url);
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
