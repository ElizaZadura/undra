import React from 'react';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { NavigationContainer } from '@react-navigation/native';
import { MaterialCommunityIcons } from '@expo/vector-icons';

import GuidesScreen from '../screens/GuidesScreen';
import ChatScreen from '../screens/ChatScreen';
import SafetyScreen from '../screens/SafetyScreen';

const Tab = createBottomTabNavigator();

export default function AppNavigator() {
  return (
    <NavigationContainer>
      <Tab.Navigator
        screenOptions={({ route }) => ({
          tabBarIcon: ({ focused, color, size }) => {
            let iconName: keyof typeof MaterialCommunityIcons.glyphMap = 'help';

            if (route.name === 'Guides') {
              iconName = focused ? 'book-open-page-variant' : 'book-open-page-variant-outline';
            } else if (route.name === 'Chat') {
              iconName = focused ? 'message-processing' : 'message-processing-outline';
            } else if (route.name === 'Safety') {
              iconName = focused ? 'shield-check' : 'shield-check-outline';
            }

            return <MaterialCommunityIcons name={iconName} size={size} color={color} />;
          },
          tabBarActiveTintColor: '#002C5B',
          tabBarInactiveTintColor: 'gray',
          headerStyle: {
            backgroundColor: '#002C5B',
          },
          headerTintColor: '#fff',
          headerTitleStyle: {
            fontWeight: 'bold',
          },
        })}
      >
        <Tab.Screen
          name="Guides"
          component={GuidesScreen}
          options={{ title: 'undra Guides' }}
        />
        <Tab.Screen
          name="Chat"
          component={ChatScreen}
          options={{ title: 'Undra AI' }}
        />
        <Tab.Screen
          name="Safety"
          component={SafetyScreen}
          options={{ title: 'Safety Net' }}
        />
      </Tab.Navigator>
    </NavigationContainer>
  );
}
