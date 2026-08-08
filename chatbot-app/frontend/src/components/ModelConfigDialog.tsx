'use client';

import React, { useState, useEffect, useMemo } from 'react';
import { ChevronDown, AudioWaveform, Search, Check, Cpu } from 'lucide-react';
import { apiGet, apiPost } from '@/lib/api-client';
import { Button } from './ui/button';
import { Input } from './ui/input';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from './ui/popover';
import type { AgentStatus } from '@/types/events';

interface ModelConfig {
  model_id: string;
}

interface AvailableModel {
  id: string;
  name: string;
  provider: string;
  description: string;
}

interface ModelConfigDialogProps {
  sessionId: string | null;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  trigger?: React.ReactNode;
  agentStatus?: AgentStatus;
  currentModelId?: string;  // Per-session model from useChat state
  onModelChange?: (modelId: string) => void;  // Callback to update per-session state
}

export function ModelConfigDialog({ sessionId, trigger, agentStatus, currentModelId, onModelChange }: ModelConfigDialogProps) {
  const [loading, setLoading] = useState(false);
  const [currentConfig, setCurrentConfig] = useState<ModelConfig | null>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  const isVoiceActive = agentStatus?.startsWith('voice_');
  const [availableModels, setAvailableModels] = useState<AvailableModel[]>([]);
  const [selectedModelId, setSelectedModelId] = useState('');

  // Load data on mount
  useEffect(() => {
    loadData();
  }, []);

  // Sync selectedModelId from currentModelId prop (per-session state takes priority)
  useEffect(() => {
    if (currentModelId) {
      setSelectedModelId(currentModelId);
    }
  }, [currentModelId]);

  // Fallback: sync from currentConfig when no prop provided
  useEffect(() => {
    if (!currentModelId && currentConfig) {
      setSelectedModelId(currentConfig.model_id);
    }
  }, [currentModelId, currentConfig]);

  // Clear search when popover closes
  useEffect(() => {
    if (!isOpen) {
      setSearchQuery('');
    }
  }, [isOpen]);

  const loadData = async () => {
    setLoading(true);
    try {
      await Promise.all([
        loadModelConfig(),
        loadAvailableModels()
      ]);
    } catch (error) {
      console.error('Failed to load data:', error);
    } finally {
      setLoading(false);
    }
  };

  const loadModelConfig = async () => {
    // If per-session model ID is provided via prop, use it instead of loading from API
    if (currentModelId) {
      setCurrentConfig({ model_id: currentModelId });
      setSelectedModelId(currentModelId);
      return;
    }

    try {
      const data = await apiGet<{ success: boolean; config: any }>(
        'model/config',
        {
          headers: sessionId ? { 'X-Session-ID': sessionId } : {},
        }
      );

      if (data.success && data.config) {
        setCurrentConfig({
          model_id: data.config.model_id,
        });
      }
    } catch (error) {
      console.error('Failed to load model config:', error);
    }
  };

  const loadAvailableModels = async () => {
    try {
      const data = await apiGet<{ models: AvailableModel[] }>(
        'model/available-models',
        {
          headers: sessionId ? { 'X-Session-ID': sessionId } : {},
        }
      );

      setAvailableModels(data.models || []);
    } catch (error) {
      console.error('Failed to load available models:', error);
    }
  };

  const handleModelChange = async (modelId: string) => {
    setSelectedModelId(modelId);
    setIsOpen(false);

    // Update per-session state via callback (this also saves global default)
    if (onModelChange) {
      onModelChange(modelId);
      setCurrentConfig({ model_id: modelId });
      return;
    }

    // Fallback: direct API call when no callback provided
    try {
      await apiPost(
        'model/config/update',
        {
          model_id: modelId,
        },
        {
          headers: sessionId ? { 'X-Session-ID': sessionId } : {},
        }
      );

      // Update currentConfig after successful API call
      setCurrentConfig({ model_id: modelId });
    } catch (error) {
      console.error('Failed to update model:', error);
      // Revert on error
      if (currentConfig) {
        setSelectedModelId(currentConfig.model_id);
      }
    }
  };

  // Filter models based on search
  const filteredModels = useMemo(() => {
    if (!searchQuery.trim()) {
      return availableModels;
    }

    // MOBILE FIX: Normalize search query (trim + lowercase) to handle mobile keyboard input
    // Mobile keyboards may add extra spaces or autocorrect issues
    const query = searchQuery.trim().toLowerCase();
    return availableModels.filter(model => {
      const nameMatch = model.name.toLowerCase().includes(query);
      const providerMatch = model.provider.toLowerCase().includes(query);
      const descMatch = model.description?.toLowerCase().includes(query);
      const idMatch = model.id.toLowerCase().includes(query);
      return nameMatch || providerMatch || descMatch || idMatch;
    });
  }, [availableModels, searchQuery]);

  // Group models by provider
  const groupedModels = useMemo(() => {
    const groups: Record<string, AvailableModel[]> = {};
    filteredModels.forEach(model => {
      const provider = model.provider || 'Other';
      if (!groups[provider]) {
        groups[provider] = [];
      }
      groups[provider].push(model);
    });
    return groups;
  }, [filteredModels]);

  const selectedModel = availableModels.find(m => m.id === selectedModelId);

  if (loading) {
    return (
      <div className="h-8 px-3 flex items-center text-label text-muted-foreground">
        Loading...
      </div>
    );
  }

  // Voice mode active - show special Nova Sonic 2 badge
  if (isVoiceActive) {
    return (
      <div className="h-8 px-3 flex items-center gap-2 rounded-md border border-primary/25 bg-accent text-label font-medium text-accent-foreground">
        <AudioWaveform className="w-4 h-4 text-primary animate-pulse" />
        <span>Nova Sonic 2</span>
      </div>
    );
  }

  return (
    <Popover open={isOpen} onOpenChange={setIsOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="h-8 px-3 text-label font-medium text-muted-foreground/70 hover:bg-muted-foreground/10 transition-all duration-200 flex items-center gap-2"
        >
          <span className="truncate max-w-[200px]">
            {selectedModel ? selectedModel.name : 'Select model'}
          </span>
          <ChevronDown className="w-3.5 h-3.5 opacity-60" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        side="top"
        className="w-[380px] max-w-[calc(100vw-2rem)] h-[450px] max-h-[60vh] p-0 shadow-lg flex flex-col"
        sideOffset={10}
      >
        {/* Header */}
        <div className="p-4 border-b shrink-0 bg-muted/30">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-body font-semibold flex items-center gap-2 text-slate-900 dark:text-slate-100">
              <Cpu className="w-4.5 h-4.5 text-slate-700 dark:text-slate-300" />
              Select Model
            </h3>
            <span className="text-caption font-medium px-2.5 py-1 rounded-full bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-800">
              {availableModels.length} available
            </span>
          </div>
          {/* Search */}
          <div className="relative">
            <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400 dark:text-slate-500" />
            <Input
              type="text"
              placeholder="Search models..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-9 h-9 text-label bg-white dark:bg-slate-950 border-slate-200 dark:border-slate-800 focus-visible:ring-2 focus-visible:ring-blue-500/20 focus-visible:border-blue-500"
            />
          </div>
        </div>

        {/* Model List */}
        <div className="flex-1 overflow-y-auto p-3">
          {Object.entries(groupedModels).length === 0 ? (
            <div className="flex items-center justify-center h-full text-label text-muted-foreground">
              No models found
            </div>
          ) : (
            <div className="space-y-4">
              {Object.entries(groupedModels).map(([provider, models]) => (
                <div key={provider}>
                  <div className="text-caption font-semibold text-muted-foreground/70 uppercase tracking-wide px-2 mb-2">
                    {provider}
                  </div>
                  <div className="space-y-1">
                    {models.map((model) => {
                      const isSelected = model.id === selectedModelId;
                      return (
                        <div
                          key={model.id}
                          onClick={() => handleModelChange(model.id)}
                          className={`group flex items-start gap-3 px-3 py-2.5 rounded-lg transition-all cursor-pointer ${
                            isSelected
                              ? 'bg-blue-50 dark:bg-blue-950/40 border border-blue-300 dark:border-blue-800/60'
                              : 'hover:bg-slate-100 dark:hover:bg-slate-800/50 border border-transparent'
                          }`}
                        >
                          <div className={`flex items-center justify-center w-5 h-5 rounded-full mt-0.5 shrink-0 ${
                            isSelected
                              ? 'bg-blue-500 dark:bg-blue-600'
                              : 'bg-slate-200 dark:bg-slate-700'
                          }`}>
                            {isSelected && <Check className="w-3 h-3 text-white" />}
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className={`text-label font-medium ${
                              isSelected
                                ? 'text-blue-900 dark:text-blue-100'
                                : 'text-slate-700 dark:text-slate-300'
                            }`}>
                              {model.name}
                            </div>
                            <div className="text-caption text-muted-foreground/70 mt-0.5 line-clamp-2">
                              {model.description}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
