import React, { useState, useRef } from 'react';
import {
  View,
  Text,
  StyleSheet,
  SafeAreaView,
  TextInput,
  TouchableOpacity,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  Image,
  ActivityIndicator
} from 'react-native';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import * as ImagePicker from 'expo-image-picker';

type Message = {
  id: string;
  sender: 'user' | 'ai';
  text: string;
  imageUri?: string;
  isRefusal?: boolean;
};

export default function ChatScreen() {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: 'welcome',
      sender: 'ai',
      text: "Hej! I am Undra, your Lund pre-arrival AI assistant. Ask me questions about waste sorting, housing corridors, laundromats, or upload a photo of a Swedish sign/notice to translate and explain!\n\n⚠️ Note: I am not allowed to advise on immigration, visas, Swedish taxes, legal disputes, or medical issues, but I will route you to official services if you ask!",
    }
  ]);
  const [inputText, setInputText] = useState('');
  const [selectedImage, setSelectedImage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const flatListRef = useRef<FlatList>(null);

  const takePhoto = async () => {
    const { status } = await ImagePicker.requestCameraPermissionsAsync();
    if (status !== 'granted') {
      alert('Sorry, we need camera permissions to make this work!');
      return;
    }

    let result = await ImagePicker.launchCameraAsync({
      mediaTypes: ['images'],
      allowsEditing: true,
      quality: 0.8,
    });

    if (!result.canceled) {
      setSelectedImage(result.assets[0].uri);
    }
  };

  const pickImage = async () => {
    const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (status !== 'granted') {
      alert('Sorry, we need camera roll permissions to make this work!');
      return;
    }

    let result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'],
      allowsEditing: true,
      quality: 0.8,
    });

    if (!result.canceled) {
      setSelectedImage(result.assets[0].uri);
    }
  };

  const handleSend = async () => {
    if (!inputText.trim() && !selectedImage) return;

    const newMessageId = Date.now().toString();
    const newUserMessage: Message = {
      id: newMessageId,
      sender: 'user',
      text: inputText.trim(),
      imageUri: selectedImage || undefined,
    };

    setMessages(prev => [...prev, newUserMessage]);
    setInputText('');
    setSelectedImage(null);
    setIsLoading(true);

    try {
      const formData = new FormData();
      if (inputText.trim()) {
        formData.append('message', inputText.trim());
      }

      if (selectedImage) {
        // Extract filename from URI
        const filename = selectedImage.split('/').pop() || 'photo.jpg';
        // Infer type from extension
        const match = /\.(\w+)$/.exec(filename);
        const type = match ? `image/${match[1]}` : `image`;

        formData.append('image', {
          uri: selectedImage,
          name: filename,
          type,
        } as any);
      }

      // Use the standard Android Emulator loopback IP for local testing.
      // In production, this would be an environment variable (e.g. https://undra.nu)
      const API_URL = Platform.OS === 'android' ? 'http://10.0.2.2:8080/api/chat' : 'http://localhost:8080/api/chat';

      const response = await fetch(API_URL, {
        method: 'POST',
        body: formData,
        headers: {
          'Accept': 'application/json',
        },
      });

      if (!response.ok) {
        throw new Error(`Server returned ${response.status}`);
      }

      const data = await response.json();

      const aiResponse: Message = {
        id: (Date.now() + 1).toString(),
        sender: 'ai',
        text: data.message || 'Sorry, I received an empty response.',
        isRefusal: data.refused,
      };

      setMessages(prev => [...prev, aiResponse]);

    } catch (error) {
      console.error(error);
      const errorResponse: Message = {
        id: (Date.now() + 1).toString(),
        sender: 'ai',
        text: "🤖 [AI-Generated Response by Undra Assistant]\n\nI am unable to reach my backend server right now. Please verify your connection!",
      };
      setMessages(prev => [...prev, errorResponse]);
    } finally {
      setIsLoading(false);
    }
  };

  const renderMessage = ({ item }: { item: Message }) => {
    const isUser = item.sender === 'user';
    return (
      <View style={[styles.messageRow, isUser ? styles.messageRowUser : styles.messageRowAi]}>
        {!isUser && (
          <View style={styles.avatarAi}>
            <Text style={{ fontSize: 16 }}>🤖</Text>
          </View>
        )}
        <View style={[
          styles.messageBubble,
          isUser ? styles.messageBubbleUser : styles.messageBubbleAi,
          item.isRefusal && styles.messageBubbleRefusal
        ]}>
          {!isUser && (
            <Text style={[styles.senderName, item.isRefusal && { color: '#B45309' }]}>
              {item.isRefusal ? '🛡️ Guardrail Refusal' : 'Undra Assistant'}
            </Text>
          )}
          {item.imageUri && (
            <Image source={{ uri: item.imageUri }} style={styles.messageImage} />
          )}
          {!!item.text && (
            <Text style={[styles.messageText, isUser ? styles.messageTextUser : styles.messageTextAi]}>
              {item.text}
            </Text>
          )}
        </View>
        {isUser && (
          <View style={styles.avatarUser}>
            <Text style={{ fontSize: 16 }}>👤</Text>
          </View>
        )}
      </View>
    );
  };

  return (
    <SafeAreaView style={styles.container}>
      <KeyboardAvoidingView
        style={styles.keyboardAvoid}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={Platform.OS === 'ios' ? 90 : 0}
      >
        <View style={styles.disclaimerBanner}>
          <Text style={styles.disclaimerText}>🤖 AI Assistant Disclosure: Responses are AI-generated.</Text>
        </View>

        <FlatList
          ref={flatListRef}
          data={messages}
          keyExtractor={item => item.id}
          renderItem={renderMessage}
          contentContainerStyle={styles.chatList}
          onContentSizeChange={() => flatListRef.current?.scrollToEnd({ animated: true })}
          onLayout={() => flatListRef.current?.scrollToEnd({ animated: true })}
        />

        {selectedImage && (
          <View style={styles.imagePreviewContainer}>
            <Image source={{ uri: selectedImage }} style={styles.imagePreview} />
            <TouchableOpacity
              style={styles.clearImageBtn}
              onPress={() => setSelectedImage(null)}
            >
              <MaterialCommunityIcons name="close-circle" size={24} color="#6B7280" />
            </TouchableOpacity>
          </View>
        )}

        <View style={styles.inputContainer}>
          <TouchableOpacity style={styles.iconButton} onPress={takePhoto}>
            <MaterialCommunityIcons name="camera" size={24} color="#6B7280" />
          </TouchableOpacity>
          <TouchableOpacity style={styles.iconButton} onPress={pickImage}>
            <MaterialCommunityIcons name="image" size={24} color="#6B7280" />
          </TouchableOpacity>

          <TextInput
            style={styles.textInput}
            value={inputText}
            onChangeText={setInputText}
            placeholder="Ask about laundry, recycling..."
            multiline
            maxLength={500}
          />

          <TouchableOpacity
            style={[styles.sendButton, (!inputText.trim() && !selectedImage) && styles.sendButtonDisabled]}
            onPress={handleSend}
            disabled={(!inputText.trim() && !selectedImage) || isLoading}
          >
            {isLoading ? (
              <ActivityIndicator size="small" color="#FFF" />
            ) : (
              <MaterialCommunityIcons name="send" size={20} color="#FFF" />
            )}
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#F4F6F9',
  },
  keyboardAvoid: {
    flex: 1,
  },
  disclaimerBanner: {
    backgroundColor: '#E5E7EB',
    paddingVertical: 8,
    paddingHorizontal: 16,
    borderBottomWidth: 1,
    borderBottomColor: '#D1D5DB',
  },
  disclaimerText: {
    fontSize: 11,
    color: '#4B5563',
    textAlign: 'center',
    fontWeight: '500',
  },
  chatList: {
    padding: 16,
    paddingBottom: 24,
  },
  messageRow: {
    flexDirection: 'row',
    marginBottom: 16,
    maxWidth: '85%',
  },
  messageRowUser: {
    alignSelf: 'flex-end',
  },
  messageRowAi: {
    alignSelf: 'flex-start',
  },
  avatarAi: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: '#002C5B',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 8,
  },
  avatarUser: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: '#C5A059',
    justifyContent: 'center',
    alignItems: 'center',
    marginLeft: 8,
  },
  messageBubble: {
    borderRadius: 16,
    padding: 12,
    maxWidth: '100%',
  },
  messageBubbleAi: {
    backgroundColor: '#FFF',
    borderWidth: 1,
    borderColor: '#E2E8F0',
  },
  messageBubbleUser: {
    backgroundColor: '#002C5B',
  },
  messageBubbleRefusal: {
    backgroundColor: '#FEF3C7',
    borderColor: '#FCD34D',
    borderWidth: 2,
  },
  senderName: {
    fontSize: 12,
    fontWeight: 'bold',
    color: '#002C5B',
    marginBottom: 4,
  },
  messageText: {
    fontSize: 14,
    lineHeight: 20,
  },
  messageTextAi: {
    color: '#1F2937',
  },
  messageTextUser: {
    color: '#FFF',
  },
  messageImage: {
    width: 200,
    height: 200,
    borderRadius: 8,
    marginBottom: 8,
  },
  imagePreviewContainer: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    padding: 12,
    backgroundColor: '#F9FAFB',
    borderTopWidth: 1,
    borderTopColor: '#E5E7EB',
  },
  imagePreview: {
    width: 60,
    height: 60,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#D1D5DB',
  },
  clearImageBtn: {
    marginLeft: 8,
  },
  inputContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 12,
    backgroundColor: '#FFF',
    borderTopWidth: 1,
    borderTopColor: '#E5E7EB',
  },
  iconButton: {
    padding: 8,
  },
  textInput: {
    flex: 1,
    backgroundColor: '#F3F4F6',
    borderRadius: 20,
    paddingHorizontal: 16,
    paddingTop: 10,
    paddingBottom: 10,
    maxHeight: 100,
    marginHorizontal: 8,
    fontSize: 15,
  },
  sendButton: {
    backgroundColor: '#002C5B',
    width: 40,
    height: 40,
    borderRadius: 20,
    justifyContent: 'center',
    alignItems: 'center',
  },
  sendButtonDisabled: {
    backgroundColor: '#9CA3AF',
  },
});
