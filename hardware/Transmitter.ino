
// Transmitter: Arduino Uno + DHT11 + Lora Module (DX-LR32)

#include <SoftwareSerial.h>
#include <DHT.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>


#define DHT_PIN   4
#define DHT_TYPE  DHT11
#define LORA_RX   2   // Received pin in Uno (connect to module TXD)
#define LORA_TX   3   // Transmitted pin in Uno (connect to module RXD)

// Time between readings
const unsigned long SEND_INTERVAL = 20000;

DHT dht(DHT_PIN, DHT_TYPE);
SoftwareSerial loraSerial(LORA_RX, LORA_TX);
LiquidCrystal_I2C lcd(0x27,16,2);

unsigned long lastSend = 0;
bool lastReading = true;

void setup() {
  Serial.begin(9600);
  loraSerial.begin(9600);
  dht.begin();
  lcd.init();
  lcd.backlight();
  Serial.println(F("Transmitter is ready"));
  delay(2000);
}

void loop() {
  if (lastReading || millis() - lastSend >= SEND_INTERVAL){
    lastReading = false;
    lastSend = millis();
    readAndSend();
  }
}

void readAndSend() {
  float humidity = dht.readHumidity();
  float temperature = dht.readTemperature();   // degrees Celsius

  if (isnan(temperature) || isnan(humidity)) {
    Serial.println(F("DHT11 read failed - check the wiring"));
    return;
  }

  // 1) Display on the Serial Monitor
  Serial.print(F("Temperature: "));
  Serial.print(temperature, 1);
  Serial.print(F(" C   Humidity: "));
  Serial.print(humidity, 1);
  Serial.println(F(" %"));

  // 2) Send through the LoRa module as one text line
  loraSerial.print(F("T:"));
  loraSerial.print(temperature, 1);
  loraSerial.print(F(",H:"));
  loraSerial.print(humidity, 1);
  loraSerial.println();
  Serial.println(F("Sent over LoRa"));

  // 3) Display on I2C
  lcd.clear();
  lcd.setCursor(0,0);
  lcd.print(F("Temp: "));
  lcd.print(temperature, 1);
  lcd.print(F(" C"));
  lcd.setCursor(0,1);
  lcd.print(F("Humid: "));
  lcd.print(humidity, 1);
  lcd.print(F(" %"));
}
