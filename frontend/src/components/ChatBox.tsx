import { useState, useRef, useEffect } from 'react';
import { chatWithAI } from '../api/client';
import { IconSend } from './icons';
import { SkeletonLines } from './Skeleton';
import type { ChatMessage } from '../types';

interface ChatBoxProps {
  contextType?: string;
  isLoggedIn?: boolean;
  emptyHint?: string;
}

export const ChatBox = ({ contextType, isLoggedIn = true, emptyHint }: ChatBoxProps) => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    setMessages([]);
  }, [contextType]);

  const handleSend = async (): Promise<void> => {
    const text = input.trim();
    if (!text || loading) return;

    const userMsg: ChatMessage = { role: 'user', message: text };
    const updatedMessages = [...messages, userMsg];
    setMessages(updatedMessages);
    setInput('');
    setLoading(true);

    try {
      const { reply } = await chatWithAI(text, contextType, updatedMessages);
      setMessages([...updatedMessages, { role: 'assistant', message: reply }]);
    } catch {
      setMessages([
        ...updatedMessages,
        { role: 'assistant', message: 'Sorry, I encountered an error. Please try again.' },
      ]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  };

  const renderInline = (text: string): Array<string | JSX.Element> => {
    return text.split(/(\*\*.*?\*\*)/g).map((part, index) => {
      const match = part.match(/^\*\*(.*?)\*\*$/);
      if (!match) return part;
      return <strong key={`${index}-${match[1]}`}>{match[1]}</strong>;
    });
  };

  const formatMessage = (text: string): JSX.Element[] => {
    return text.split('\n').map((line, index) => {
      if (!line) {
        return <span key={index} className="block">{' '}</span>;
      }

      const numbered = line.match(/^(\d+)\.\s(.*)$/);
      if (numbered) {
        return (
          <span key={`${index}-${line}`} className="block">
            <span className="font-semibold tabular-nums">{numbered[1]}.</span>{' '}
            {renderInline(numbered[2])}
          </span>
        );
      }

      if (line.startsWith('- ') || line.startsWith('* ')) {
        return (
          <span key={`${index}-${line}`} className="block pl-3">
            {renderInline(line.slice(2))}
          </span>
        );
      }

      return <span key={`${index}-${line}`} className="block">{renderInline(line)}</span>;
    });
  };

  return (
    <section className="border border-base-300 flex flex-col">
      <h2 className="px-3 py-2 border-b border-base-300 bg-base-200 font-display text-lg font-semibold uppercase tracking-wide">
        Ask Claude
      </h2>

      <div className="flex-1 overflow-y-auto min-h-[200px] max-h-[360px]">
        {messages.length === 0 && (
          <p className="text-sm text-muted p-4">
            {isLoggedIn
              ? emptyHint ?? 'Ask about your fantasy team, player stats, or a trade.'
              : 'Sign in to ask Claude questions.'}
          </p>
        )}

        {messages.length > 0 && (
          <ol className="divide-y divide-base-300">
            {messages.map((msg, i) => (
              <li key={i} className="px-3 py-2.5">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted mb-0.5">
                  {msg.role === 'user' ? 'You' : 'Claude'}
                </p>
                <div className="text-sm leading-relaxed">
                  {msg.role === 'assistant' ? formatMessage(msg.message) : msg.message}
                </div>
              </li>
            ))}
            {loading && (
              <li className="px-3 py-2.5">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted mb-1">
                  Claude
                </p>
                <SkeletonLines lines={2} label="Claude is replying" />
              </li>
            )}
          </ol>
        )}

        <div ref={messagesEndRef} />
      </div>

      <div className="px-3 py-2.5 border-t border-base-300 bg-base-200">
        <div className="flex items-center gap-2">
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSend()}
            placeholder={isLoggedIn ? 'Ask a question' : 'Sign in to chat'}
            aria-label="Message for Claude"
            className="input input-bordered input-sm flex-1"
            disabled={loading || !isLoggedIn}
          />
          <button
            onClick={handleSend}
            disabled={loading || !input.trim() || !isLoggedIn}
            className="btn btn-primary btn-sm gap-1.5"
          >
            <IconSend size={14} />
            Send
          </button>
        </div>
      </div>
    </section>
  );
};
