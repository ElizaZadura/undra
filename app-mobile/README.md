# Undra Mobile App

This is the native mobile application for Undra, built with React Native and Expo. It provides a mobile-first native experience for the Lund Pre-Arrival Assistant.

## Milestone 1 Implementation
- **Bottom Tab Navigation:** Guides, Chat (stub), and Safety Net (stub).
- **Guides Tab:** Ported static guide data. Features a list of guides and a full-screen modal for guide content.

## Setup & Running Locally

1. **Install dependencies:**
   \`\`\`bash
   cd app-mobile
   npm install
   \`\`\`

2. **Start the Expo server:**
   \`\`\`bash
   npm start
   \`\`\`
   or
   \`\`\`bash
   npx expo start
   \`\`\`

3. **Run on a device or emulator:**
   - **iOS:** Press \`i\` in the terminal to open the iOS Simulator (requires a Mac with Xcode).
   - **Android:** Press \`a\` in the terminal to open the Android Emulator (requires Android Studio).
   - **Physical Device:** Download the "Expo Go" app on your iOS or Android device and scan the QR code printed in the terminal.

## Roadmap (Milestone 2+)
- Connect ChatScreen to the existing FastAPI backend (`/api/chat`).
- Implement native camera integration for the photo-upload feature.
- Port SafetyScreen official routing links.