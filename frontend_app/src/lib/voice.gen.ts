/* Generated from backend/aethel/voice/protocol.py by pnpm gen:types. Do not edit. */

export type Client = Hello | SpeechStart | SpeechEnd | Interrupt | EndCall;
export type ConversationId = string;
export type Type = "hello";
export type Type1 = "speech_start";
export type DurationMs = number;
export type Misfire = boolean;
export type Type2 = "speech_end";
export type LastStartedSentenceId = number | null;
export type TurnId = string | null;
export type Type3 = "interrupt";
export type Type4 = "end_call";
export type Server = Ready | Caption | Thinking | TtsMeta | TurnEnd | VoiceError;
export type PersonaName = string;
export type Stt = string;
export type TtsVoice = string;
export type Type5 = "ready";
export type Final = boolean;
export type MessageId = string | null;
export type Role = "user" | "assistant";
export type Text = string;
export type Type6 = "caption";
export type Type7 = "thinking";
export type Codec = "pcm16";
export type SampleRate = number;
export type SentenceId = number;
export type Text1 = string;
export type TurnId1 = string;
export type Type8 = "tts_meta";
export type Status = "complete" | "interrupted" | "stopped" | "error";
export type TurnId2 = string;
export type Type9 = "turn_end";
export type Fatal = boolean;
export type Message = string;
export type Type10 = "voice_error";

export interface VoiceProtocol {
  client: Client;
  server: Server;
  [k: string]: unknown;
}
export interface Hello {
  conversation_id: ConversationId;
  type: Type;
}
export interface SpeechStart {
  type: Type1;
}
/**
 * The user stopped speaking. The utterance's audio follows as a binary frame, unless `misfire`: the voice
 * detector fired on a noise and there is nothing to transcribe.
 */
export interface SpeechEnd {
  duration_ms: DurationMs;
  misfire: Misfire;
  type: Type2;
}
/**
 * The user spoke over the reply: playback has stopped. `last_started_sentence_id` is the last sentence that began
 * to play (None: none had), which is what the saved reply is cut to.
 */
export interface Interrupt {
  last_started_sentence_id: LastStartedSentenceId;
  turn_id: TurnId;
  type: Type3;
}
export interface EndCall {
  type: Type4;
}
export interface Ready {
  persona_name: PersonaName;
  stt: Stt;
  tts_voice: TtsVoice;
  type: Type5;
}
export interface Caption {
  final: Final;
  message_id: MessageId;
  role: Role;
  text: Text;
  type: Type6;
}
/**
 * Aethel has decided it's its turn; the first audio follows.
 */
export interface Thinking {
  type: Type7;
}
export interface TtsMeta {
  codec: Codec;
  sample_rate: SampleRate;
  sentence_id: SentenceId;
  text: Text1;
  turn_id: TurnId1;
  type: Type8;
}
export interface TurnEnd {
  status: Status;
  turn_id: TurnId2;
  type: Type9;
}
export interface VoiceError {
  fatal: Fatal;
  message: Message;
  type: Type10;
}
