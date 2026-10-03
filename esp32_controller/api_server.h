#pragma once

// Starts the HTTP REST server. Safe to call before Wi-Fi is connected:
// the server becomes reachable as soon as Wi-Fi comes up.
void apiBegin();

// Call every loop() iteration. Never blocks for long.
void apiHandle();
