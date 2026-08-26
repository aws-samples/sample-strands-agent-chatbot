import React, { useEffect, useState } from 'react'
import { ActivityIndicator, Alert, KeyboardAvoidingView, Platform, Pressable, StyleSheet, Text, View } from 'react-native'
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context'
import { Ionicons } from '@expo/vector-icons'
import AsyncStorage from '@react-native-async-storage/async-storage'
import type { ImagePickerAsset } from 'expo-image-picker'
import { useChat } from '../../hooks/useChat'
import { useTheme } from '../../context/ThemeContext'
import { AVAILABLE_MODELS, DEFAULT_MODEL_ID, MODEL_STORAGE_KEY, normalizeModelId } from '../../lib/constants'
import MessageList from './MessageList'
import ChatInputBar from './ChatInputBar'
import GreetingScreen from './GreetingScreen'
import ModelPickerSheet from './ModelPickerSheet'
import InterruptCard from '../events/InterruptCard'
import OAuthElicitationModal from './OAuthElicitationModal'

interface Props {
  sessionId: string
  onSessionTitleChange?: (title: string) => void
  onTitleUpdated?: () => void
  onMenuPress?: () => void
  onNewChat?: () => void
  onSignOut?: () => void
}

export default function ChatScreen({
  sessionId,
  onSessionTitleChange,
  onTitleUpdated,
  onMenuPress,
  onNewChat,
  onSignOut,
}: Props) {
  const { colors, toggleTheme, isDark, isDetailed, setDisplayMode } = useTheme()
  const [selectedModelId, setSelectedModelId] = useState(DEFAULT_MODEL_ID)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [modelPickerOpen, setModelPickerOpen] = useState(false)
  const insets = useSafeAreaInsets()

  // Load persisted model selection
  useEffect(() => {
    AsyncStorage.getItem(MODEL_STORAGE_KEY).then(saved => {
      const normalized = saved ? normalizeModelId(saved) : null
      if (normalized && AVAILABLE_MODELS.some(model => model.id === normalized)) {
        setSelectedModelId(normalized)
        if (normalized !== saved) {
          AsyncStorage.setItem(MODEL_STORAGE_KEY, normalized)
        }
      } else if (saved) {
        AsyncStorage.setItem(MODEL_STORAGE_KEY, DEFAULT_MODEL_ID)
      }
    })
  }, [])

  const handleModelSelect = (modelId: string) => {
    setSelectedModelId(modelId)
    AsyncStorage.setItem(MODEL_STORAGE_KEY, modelId)
  }

  const {
    messages,
    agentStatus,
    thinkingMessage,
    isSending,
    networkError,
    pendingOAuth,
    pendingInterrupt,
    sendMessage,
    stopStream,
    hasMore,
    loadMore,
    loadHistory,
    dismissInterrupt,
    dismissOAuth,
    isReconnecting,
    reconnectAttempt,
  } = useChat({
    sessionId,
    modelId: selectedModelId,
    onTitleUpdated: (title) => {
      onSessionTitleChange?.(title)
      onTitleUpdated?.()
    },
  })

  useEffect(() => {
    console.log('[ChatScreen] sessionId changed:', sessionId)
    loadHistory()
  }, [sessionId])

  useEffect(() => {
    if (networkError) Alert.alert('Connection error', networkError)
  }, [networkError])

  useEffect(() => {
    const firstUser = messages.find(m => m.role === 'user')
    if (firstUser && onSessionTitleChange) {
      onSessionTitleChange(firstUser.text.slice(0, 40))
    }
  }, [messages.length])

  const isThinking = agentStatus === 'thinking'
  const isStreaming = agentStatus !== 'idle' || isSending
  const showGreeting = messages.length === 0 && !isThinking && !isSending

  const selectedModelName =
    AVAILABLE_MODELS.find(m => m.id === selectedModelId)?.name ?? 'Model'

  const handleSend = (text: string, images?: ImagePickerAsset[], documents?: import('../../types/chat').PickedDocument[]) => {
    sendMessage(text, images, documents)
  }

  return (
    <SafeAreaView style={[styles.safeArea, { backgroundColor: colors.bg }]} edges={['top', 'left', 'right']}>
      <KeyboardAvoidingView 
        style={{ flex: 1 }} 
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={0}
      >
        <View style={styles.container}>
          {/* ── Header ── */}
          <View style={[styles.header, { borderBottomColor: colors.border, backgroundColor: colors.bg }]}>
            {onMenuPress && (
              <Pressable style={[styles.headerBtn, { backgroundColor: colors.bgSecondary }]} onPress={onMenuPress}>
                <Ionicons name="menu-outline" size={20} color={colors.text} />
              </Pressable>
            )}
            <Text style={[styles.headerTitle, { color: colors.text }]}>Cord</Text>
            <View style={styles.headerRight}>
              {onNewChat && (
                <Pressable style={[styles.headerBtn, { backgroundColor: colors.bgSecondary }]} onPress={onNewChat}>
                  <Ionicons name="create-outline" size={19} color={colors.text} />
                </Pressable>
              )}
              <Pressable
                style={[styles.headerBtn, { backgroundColor: colors.bgSecondary }]}
                onPress={() => setSettingsOpen(o => !o)}
              >
                <Ionicons name="settings-outline" size={19} color={colors.text} />
              </Pressable>
            </View>
          </View>

          {/* ── Settings dropdown ── */}
          {settingsOpen && (
            <View style={[styles.settingsDropdown, { backgroundColor: colors.surface, borderColor: colors.border }]}>
              {/* Model selection */}
              <Pressable
                style={styles.settingsItem}
                onPress={() => {
                  setSettingsOpen(false)
                  setModelPickerOpen(true)
                }}
              >
                <Ionicons name="hardware-chip-outline" size={18} color={colors.text} />
                <View style={styles.settingsModelInfo}>
                  <Text style={[styles.settingsText, { color: colors.text }]}>Model</Text>
                  <Text style={[styles.settingsSubtext, { color: colors.textMuted }]} numberOfLines={1}>
                    {selectedModelName}
                  </Text>
                </View>
                <Ionicons name="chevron-forward" size={14} color={colors.textMuted} />
              </Pressable>

              <Pressable
                style={[styles.settingsItem, { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border }]}
                onPress={() => {
                  setSettingsOpen(false)
                  toggleTheme()
                }}
              >
                <Ionicons name={isDark ? 'sunny-outline' : 'moon-outline'} size={18} color={colors.text} />
                <Text style={[styles.settingsText, { color: colors.text }]}>
                  {isDark ? 'Light mode' : 'Dark mode'}
                </Text>
              </Pressable>

              <Pressable
                style={[styles.settingsItem, { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border }]}
                onPress={() => {
                  setSettingsOpen(false)
                  setDisplayMode(isDetailed ? 'minimal' : 'detailed')
                }}
              >
                <Ionicons name={isDetailed ? 'eye-off-outline' : 'eye-outline'} size={18} color={colors.text} />
                <Text style={[styles.settingsText, { color: colors.text }]}>
                  {isDetailed ? 'Minimal view' : 'Detailed view'}
                </Text>
              </Pressable>

              {onSignOut && (
                <Pressable
                  style={[styles.settingsItem, { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border }]}
                  onPress={() => {
                    setSettingsOpen(false)
                    onSignOut()
                  }}
                >
                  <Ionicons name="log-out-outline" size={18} color={colors.error} />
                  <Text style={[styles.settingsText, { color: colors.error }]}>Sign out</Text>
                </Pressable>
              )}
            </View>
          )}

          {isReconnecting && (
            <View style={[styles.reconnectBanner, { backgroundColor: colors.warningBg }]}>
              <ActivityIndicator size="small" color={colors.warningText} />
              <Text style={[styles.reconnectText, { color: colors.warningText }]}>
                Reconnecting… (attempt {reconnectAttempt}/5)
              </Text>
            </View>
          )}

          <View style={{ flex: 1 }} onTouchStart={() => settingsOpen && setSettingsOpen(false)}>
            {showGreeting ? (
              <GreetingScreen />
            ) : (
              <MessageList
                messages={messages}
                isThinking={isThinking}
                thinkingLabel={thinkingMessage}
                hasMore={hasMore}
                onLoadMore={loadMore}
              />
            )}
          </View>

          {pendingInterrupt && (
            <View style={[styles.interruptWrapper, { backgroundColor: colors.bg }]}>
              <InterruptCard
                interrupts={pendingInterrupt.interrupts}
                onApprove={() => dismissInterrupt(true)}
                onReject={() => dismissInterrupt(false)}
              />
            </View>
          )}

          <ChatInputBar
            onSend={handleSend}
            onStop={stopStream}
            isStreaming={isStreaming}
            disabled={!!pendingInterrupt}
          />
        </View>
      </KeyboardAvoidingView>

      <OAuthElicitationModal oauth={pendingOAuth} onComplete={dismissOAuth} />

      {/* Model picker bottom sheet — rendered above everything */}
      {modelPickerOpen && (
        <ModelPickerSheet
          selectedModelId={selectedModelId}
          onSelect={handleModelSelect}
          onClose={() => setModelPickerOpen(false)}
        />
      )}
    </SafeAreaView>
  )
}

const styles = StyleSheet.create({
  safeArea: { flex: 1 },
  container: { flex: 1 },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 8,
  },
  headerTitle: { flex: 1, fontSize: 17, fontWeight: '700' },
  headerRight: { flexDirection: 'row', gap: 6 },
  headerBtn: {
    width: 34,
    height: 34,
    borderRadius: 8,
    alignItems: 'center',
    justifyContent: 'center',
  },
  settingsDropdown: {
    position: 'absolute',
    top: 56,
    right: 12,
    zIndex: 50,
    borderRadius: 12,
    borderWidth: 1,
    paddingVertical: 4,
    minWidth: 200,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.12,
    shadowRadius: 12,
    elevation: 8,
  },
  settingsItem: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 14,
    paddingVertical: 11,
    gap: 10,
  },
  settingsText: { fontSize: 14, fontWeight: '500', flex: 1 },
  settingsSubtext: { fontSize: 12 },
  settingsModelInfo: { flex: 1, gap: 1 },
  reconnectBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 6,
    paddingHorizontal: 12,
    gap: 6,
  },
  reconnectText: { fontSize: 12, fontWeight: '500' },
  interruptWrapper: {
    paddingHorizontal: 12,
    paddingBottom: 4,
  },
})
