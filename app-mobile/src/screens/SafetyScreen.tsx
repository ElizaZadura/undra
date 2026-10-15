import React from 'react';
import { View, Text, StyleSheet, SafeAreaView, ScrollView, TouchableOpacity, Linking } from 'react-native';
import { MaterialCommunityIcons } from '@expo/vector-icons';

export default function SafetyScreen() {

  const openUrl = (url: string) => {
    Linking.openURL(url);
  };

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView contentContainerStyle={styles.scrollContent}>

        <View style={styles.warningBanner}>
          <View style={styles.warningHeader}>
            <MaterialCommunityIcons name="alert-circle-outline" size={20} color="#B45309" />
            <Text style={styles.warningTitle}>Official Sweden Guardrails Portal</Text>
          </View>
          <Text style={styles.warningText}>
            By operating charter policy, Undra Assistant declines to advise on legal, immigration, tax, and healthcare matters due to the high risks of automated advice on visa conditions and legal standing. Please contact the official authorities below directly.
          </Text>
        </View>

        {/* Migrationsverket */}
        <View style={styles.card}>
          <View style={[styles.badge, { backgroundColor: '#EFF6FF' }]}>
            <Text style={[styles.badgeText, { color: '#1E40AF' }]}>IMMIGRATION & VISAS</Text>
          </View>
          <Text style={styles.cardTitle}>Migrationsverket</Text>
          <Text style={styles.cardBody}>
            Handles residence permits, student visas, visa status inquiries, and study conditions. Any queries regarding your permission to study or remain in Sweden must go here.
          </Text>
          <TouchableOpacity
            style={[styles.button, { backgroundColor: '#EFF6FF', borderColor: '#BFDBFE' }]}
            onPress={() => openUrl('https://www.migrationsverket.se')}
          >
            <Text style={[styles.buttonText, { color: '#1D4ED8' }]}>Visit Migrationsverket Website ↗</Text>
          </TouchableOpacity>
        </View>

        {/* Skatteverket */}
        <View style={styles.card}>
          <View style={[styles.badge, { backgroundColor: '#FEF3C7' }]}>
            <Text style={[styles.badgeText, { color: '#92400E' }]}>TAXES & POPULATION REGISTER</Text>
          </View>
          <Text style={styles.cardTitle}>Skatteverket</Text>
          <Text style={styles.cardBody}>
            Manages folkbokföring (population registration), tax ID numbers (personnummer), coordination numbers (samordningsnummer), and income tax declarations.
          </Text>
          <TouchableOpacity
            style={[styles.button, { backgroundColor: '#FEF3C7', borderColor: '#FDE68A' }]}
            onPress={() => openUrl('https://www.skatteverket.se')}
          >
            <Text style={[styles.buttonText, { color: '#92400E' }]}>Visit Skatteverket Website ↗</Text>
          </TouchableOpacity>
        </View>

        {/* Housing Contracts */}
        <View style={styles.card}>
          <View style={[styles.badge, { backgroundColor: '#ECFDF5' }]}>
            <Text style={[styles.badgeText, { color: '#065F46' }]}>HOUSING & STUDENT WELFARE</Text>
          </View>
          <Text style={styles.cardTitle}>AF Bostäder & International Desk</Text>
          <Text style={styles.cardBody}>
            For legal questions about lease contracts, security deposits, sub-letting agreements, or disputes with landlords, contact your student housing provider or the university.
          </Text>
          <View style={styles.buttonRow}>
            <TouchableOpacity
              style={[styles.halfButton, { backgroundColor: '#ECFDF5', borderColor: '#A7F3D0' }]}
              onPress={() => openUrl('https://www.afbostader.se')}
            >
              <Text style={[styles.buttonText, { color: '#065F46' }]}>AF Bostäder ↗</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.halfButton, { backgroundColor: '#EEF2FF', borderColor: '#C7D2FE' }]}
              onPress={() => openUrl('https://www.lunduniversity.lu.se/student-life/preparing-come/international-desk')}
            >
              <Text style={[styles.buttonText, { color: '#3730A3' }]}>Int. Desk ↗</Text>
            </TouchableOpacity>
          </View>
        </View>

        {/* Medical */}
        <View style={styles.card}>
          <View style={[styles.badge, { backgroundColor: '#FEF2F2' }]}>
            <Text style={[styles.badgeText, { color: '#991B1B' }]}>MEDICAL & SAFETY SERVICES</Text>
          </View>
          <Text style={styles.cardTitle}>1177 Vårdguiden</Text>
          <Text style={styles.cardBody}>
            Handles non-emergency health concerns, finding local clinics (vårdcentral), and medical consultations. For any threat to life or emergency safety matter, call 112 immediately.
          </Text>
          <View style={styles.buttonRow}>
            <TouchableOpacity
              style={[styles.halfButton, { backgroundColor: '#FEF2F2', borderColor: '#FECACA' }]}
              onPress={() => openUrl('https://www.1177.se')}
            >
              <Text style={[styles.buttonText, { color: '#991B1B' }]}>1177 Healthcare ↗</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.halfButton, { backgroundColor: '#DC2626', borderColor: '#DC2626' }]}
              onPress={() => openUrl('tel:112')}
            >
              <Text style={[styles.buttonText, { color: '#FFF' }]}>Call 112</Text>
            </TouchableOpacity>
          </View>
        </View>

      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#F4F6F9',
  },
  scrollContent: {
    padding: 16,
    paddingBottom: 32,
  },
  warningBanner: {
    backgroundColor: '#FFFBEB',
    borderWidth: 1,
    borderColor: '#FDE68A',
    borderRadius: 12,
    padding: 16,
    marginBottom: 16,
  },
  warningHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 8,
  },
  warningTitle: {
    fontWeight: 'bold',
    color: '#92400E',
    marginLeft: 8,
    fontSize: 14,
  },
  warningText: {
    fontSize: 13,
    color: '#B45309',
    lineHeight: 18,
  },
  card: {
    backgroundColor: '#FFF',
    borderRadius: 12,
    padding: 16,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: '#E2E8F0',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.05,
    shadowRadius: 2,
    elevation: 2,
  },
  badge: {
    alignSelf: 'flex-start',
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 4,
    marginBottom: 8,
  },
  badgeText: {
    fontSize: 10,
    fontWeight: 'bold',
    letterSpacing: 0.5,
  },
  cardTitle: {
    fontSize: 16,
    fontWeight: 'bold',
    color: '#111827',
    marginBottom: 6,
  },
  cardBody: {
    fontSize: 13,
    color: '#4B5563',
    lineHeight: 18,
    marginBottom: 16,
  },
  button: {
    paddingVertical: 10,
    borderRadius: 8,
    alignItems: 'center',
    borderWidth: 1,
  },
  buttonRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: 8,
  },
  halfButton: {
    flex: 1,
    paddingVertical: 10,
    borderRadius: 8,
    alignItems: 'center',
    borderWidth: 1,
  },
  buttonText: {
    fontSize: 13,
    fontWeight: 'bold',
  },
});
