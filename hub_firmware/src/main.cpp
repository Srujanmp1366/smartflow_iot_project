/*
  SmartFlow — Highway Toll and Traffic Monitoring System
  Hardware: Arduino Mega 2560
            6x FC-51 IR sensors (gates OE1, OE2, OE3, IE1, IE2, IE3)
            3x HC-SR04 ultrasonic sensors (US1, US2, US3)
            16x2 LCD I2C
            3x LEDs (Green, Yellow, Red) with 220Ω resistors
            1x Buzzer
*/
#include <Arduino.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

// ══════════════════════════════════════════════════════════════
// PIN DEFINITIONS
// ══════════════════════════════════════════════════════════════

// IR gate sensors (gates for entry/exit detection)
const int NUM_IR = 6;
const int IR_PINS[NUM_IR]      = {22, 23, 24, 25, 26, 27};
const char* GATE_NAMES[NUM_IR] = {"OE1","OE2","OE3","IE1","IE2","IE3"};

// Ultrasonic sensors (for speed measurement)
const int US_TRIG[3] = {30, 32, 34};
const int US_ECHO[3] = {31, 33, 35};

// Output devices
#define LED_GREEN  4    // Free flow indicator
#define LED_YELLOW 5    // Slow traffic indicator
#define LED_RED    6    // Congestion/jam indicator
#define BUZZER     7    // Jam alert buzzer

// LCD Display
LiquidCrystal_I2C lcd(0x27, 16, 2);

// ══════════════════════════════════════════════════════════════
// CONSTANTS
// ══════════════════════════════════════════════════════════════

const float CM_TO_KM      = 1.32;   // Scale: 152cm track = 200km highway
const float TOLL_PER_KM   = 2.5;    // Toll rate in rupees per km
const float CAR_THRESHOLD = 6.0;    // Ultrasonic distance threshold (cm)
const float ROAD_HEIGHT   = 12.0;   // Empty road ultrasonic reading (cm)
const int   MAX_CARS      = 4;      // Maximum 4 toy cars in demo
const unsigned long DEBOUNCE_MS = 1500;  // Gate debounce time

// ══════════════════════════════════════════════════════════════
// CAR TRACKING DATA STRUCTURE
// ══════════════════════════════════════════════════════════════

struct Car {
  int           id;            // Unique car ID (1, 2, 3, 4)
  int           entryGate;     // Which gate car entered from (0-5)
  unsigned long entryTime;     // Timestamp of entry (milliseconds)
  float         totalDistCm;   // Total distance travelled (cm)
  float         speedKmh;      // Last measured speed (km/h)
  bool          active;        // Is this car slot in use?
  bool          onRoad;        // Is car currently on the highway?
};

Car cars[MAX_CARS];
int nextId = 1;  // Next car ID to assign

// ══════════════════════════════════════════════════════════════
// SENSOR STATE TRACKING
// ══════════════════════════════════════════════════════════════

// IR gate sensor state
bool          prevIR[NUM_IR]      = {false};
unsigned long lastTrigger[NUM_IR] = {0};

// Ultrasonic sensor state
float         prevUsDist[3]       = {20, 20, 20};
unsigned long prevUsTime[3]       = {0, 0, 0};

// ══════════════════════════════════════════════════════════════
// HELPER FUNCTIONS
// ══════════════════════════════════════════════════════════════

// Find first free car slot
int freeSlot() {
  for (int i = 0; i < MAX_CARS; i++)
    if (!cars[i].active) return i;
  return -1;
}

// Count cars currently on road
int carsOnRoad() {
  int count = 0;
  for (int i = 0; i < MAX_CARS; i++)
    if (cars[i].active && cars[i].onRoad) count++;
  return count;
}

// Read ultrasonic distance in cm
float readUS(int idx) {
  digitalWrite(US_TRIG[idx], LOW);
  delayMicroseconds(2);
  digitalWrite(US_TRIG[idx], HIGH);
  delayMicroseconds(10);
  digitalWrite(US_TRIG[idx], LOW);
  
  long duration = pulseIn(US_ECHO[idx], HIGH, 25000);
  if (duration == 0) return ROAD_HEIGHT;
  
  return (duration * 0.034) / 2.0;
}

// Send CSV data to serial for Python backend
void sendSerial(String event, int carId, int gateOrSensor,
                float speed, String extra) {
  Serial.print(event);       Serial.print(",");
  Serial.print(carId);       Serial.print(",");
  Serial.print(gateOrSensor);Serial.print(",");
  Serial.print(speed, 1);    Serial.print(",");
  Serial.print(extra);       Serial.print(",");
  Serial.println(millis());
}

// Update LCD display
void updateLCD(String line1, String line2) {
  lcd.setCursor(0, 0);
  lcd.print(line1);
  // Pad with spaces to clear previous text
  for (int i = line1.length(); i < 16; i++) lcd.print(" ");
  
  lcd.setCursor(0, 1);
  lcd.print(line2);
  for (int i = line2.length(); i < 16; i++) lcd.print(" ");
}

// Control traffic light LEDs based on state
void setTrafficLight(String state) {
  digitalWrite(LED_GREEN,  state == "FREE"       ? HIGH : LOW);
  digitalWrite(LED_YELLOW, state == "SLOW"       ? HIGH : LOW);
  digitalWrite(LED_RED,   (state == "CONGESTED" ||
                            state == "JAM")      ? HIGH : LOW);
  
  if (state == "JAM") {
    tone(BUZZER, 1000, 500);  // 1kHz tone for 500ms
  }
}

// ══════════════════════════════════════════════════════════════
// SETUP
// ══════════════════════════════════════════════════════════════

void setup() {
  Serial.begin(115200);
  
  // Configure IR sensor pins
  for (int i = 0; i < NUM_IR; i++) {
    pinMode(IR_PINS[i], INPUT_PULLUP);
  }
  
  // Configure ultrasonic sensor pins
  for (int i = 0; i < 3; i++) {
    pinMode(US_TRIG[i], OUTPUT);
    pinMode(US_ECHO[i], INPUT);
  }
  
  // Configure output pins
  pinMode(LED_GREEN,  OUTPUT);
  pinMode(LED_YELLOW, OUTPUT);
  pinMode(LED_RED,    OUTPUT);
  pinMode(BUZZER,     OUTPUT);
  
  // Initial LED state - green (free flow)
  digitalWrite(LED_GREEN,  HIGH);
  digitalWrite(LED_YELLOW, LOW);
  digitalWrite(LED_RED,    LOW);
  
  // Initialize LCD
  lcd.init();
  lcd.backlight();
  updateLCD("SmartFlow Ready", "0 cars on road");
  
  delay(1000);
  
  // Send CSV header for Python backend
  Serial.println("event,car_id,gate_sensor,speed_kmh,extra,ts_ms");
}

// ══════════════════════════════════════════════════════════════
// MAIN LOOP
// ══════════════════════════════════════════════════════════════

void loop() {
  unsigned long now = millis();
  
  // ──────────────────────────────────────────────────────────
  // PROCESS IR GATE SENSORS
  // ──────────────────────────────────────────────────────────
  
  for (int i = 0; i < NUM_IR; i++) {
    // Read sensor (LOW = object detected for FC-51)
    bool triggered = (digitalRead(IR_PINS[i]) == LOW);
    
    // Detect rising edge with debounce
    if (triggered && !prevIR[i] &&
       (now - lastTrigger[i]) > DEBOUNCE_MS) {
      
      lastTrigger[i] = now;
      
      // Check if this is an EXIT event
      bool isExit  = false;
      int  exitIdx = -1;
      
      for (int j = 0; j < MAX_CARS; j++) {
        if (!cars[j].active || !cars[j].onRoad) continue;
        
        // Same type gate check (outside gates 0-2, inside gates 3-5)
        bool sameType = 
          (i < 3 && cars[j].entryGate < 3) ||
          (i >= 3 && cars[j].entryGate >= 3);
        
        // Different gate = exit
        if (sameType && cars[j].entryGate != i) {
          isExit  = true;
          exitIdx = j;
          break;
        }
      }
      
      if (!isExit) {
        // ════════════════════════════════════════════════════
        // ENTRY EVENT
        // ════════════════════════════════════════════════════
        
        int slot = freeSlot();
        if (slot >= 0) {
          cars[slot].id          = nextId++;
          cars[slot].entryGate   = i;
          cars[slot].entryTime   = now;
          cars[slot].totalDistCm = 0;
          cars[slot].speedKmh    = 0;
          cars[slot].active      = true;
          cars[slot].onRoad      = true;
          
          sendSerial("ENTRY", cars[slot].id, i, 0,
                     String(GATE_NAMES[i]));
          
          updateLCD(
            "ENTRY:" + String(GATE_NAMES[i]) +
            " C#" + String(cars[slot].id),
            "Cars:" + String(carsOnRoad())
          );
        }
        
      } else {
        // ════════════════════════════════════════════════════
        // EXIT EVENT
        // ════════════════════════════════════════════════════
        
        if (exitIdx >= 0) {
          Car &c = cars[exitIdx];
          
          // Calculate toll
          float realKm = c.totalDistCm * CM_TO_KM;
          float toll   = realKm * TOLL_PER_KM;
          
          String exitExtra =
            String(GATE_NAMES[i]) + ":" +
            String(realKm, 1) + "km:Rs" +
            String(toll, 2);
          
          sendSerial("EXIT", c.id, i, c.speedKmh, exitExtra);
          
          updateLCD(
            "EXIT:" + String(GATE_NAMES[i]) +
            " C#" + String(c.id),
            "Rs" + String(toll, 1) +
            " " + String(realKm, 0) + "km"
          );
          
          // Mark car as off road
          c.active = false;
          c.onRoad = false;
        }
      }
    }
    
    prevIR[i] = triggered;
  }
  
  // ──────────────────────────────────────────────────────────
  // PROCESS ULTRASONIC SENSORS (SPEED MEASUREMENT)
  // ──────────────────────────────────────────────────────────
  
  for (int i = 0; i < 3; i++) {
    float dist    = readUS(i);
    bool  carHere = (dist < CAR_THRESHOLD);
    bool  wasHere = (prevUsDist[i] < CAR_THRESHOLD);
    
    // Detect car just entering sensor zone
    if (carHere && !wasHere) {
      unsigned long dt    = now - prevUsTime[i];
      float         moved = 40.0;  // Distance between sensors (cm)
      float         speed = 0;
      
      // Calculate speed (avoid division by very small dt)
      if (dt > 50 && dt < 15000) {
        float cmPerSec = (moved / (float)dt) * 1000.0;
        speed = cmPerSec * CM_TO_KM * 3.6;  // Convert to km/h
      }
      
      // Update nearest active car's speed and distance
      for (int j = 0; j < MAX_CARS; j++) {
        if (cars[j].active && cars[j].onRoad) {
          cars[j].speedKmh    = speed;
          cars[j].totalDistCm += moved;
          break;  // Update only first active car
        }
      }
      
      sendSerial("SPEED", 0, i, speed, "us" + String(i));
      prevUsTime[i] = now;
    }
    
    prevUsDist[i] = dist;
  }
  
  // ──────────────────────────────────────────────────────────
  // PERIODIC LCD UPDATE (every 4 seconds)
  // ──────────────────────────────────────────────────────────
  
  static unsigned long lastLcd = 0;
  if (now - lastLcd > 4000) {
    lastLcd = now;
    updateLCD(
      "SmartFlow Active",
      "Cars:" + String(carsOnRoad()) + " on road"
    );
  }
  
  delay(20);  // Small delay for sensor stability
}