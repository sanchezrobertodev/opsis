"use client";

import { Player, type PlayerRef } from "@remotion/player";
import { linearTiming, TransitionSeries } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { forwardRef, type ReactNode } from "react";
import { AbsoluteFill, Audio, Html5Video, Sequence } from "remotion";
import { FPS, type TitleCard, type Track } from "@/lib/cut";

/** One item on the picture track, in frames (see lib/cut.ts:sequence). */
export type PreviewItem = {
  key: string; frames: number; overlapIn: number; overlapOut: number;
  take?: { src: string; trimBefore: number; volume: number };
  card?: TitleCard;
};
/** One clip on an audio track, in frames. */
export type PreviewSound = {
  key: string; track: Track; src: string; from: number; frames: number; trimBefore: number; volume: number;
  fadeIn: number; fadeOut: number;
};
type FilmProps = { items: PreviewItem[]; sounds: PreviewSound[]; duck: boolean };

const DUCKED = 0.35; // about what the export's compressor takes off the music under speech

/** 0..1 over a fade in at the start and a fade out at the end of `frames`. */
function ramp(f: number, frames: number, fadeIn: number, fadeOut: number) {
  const up = fadeIn > 0 ? Math.min(1, (f + 1) / fadeIn) : 1;
  const down = fadeOut > 0 ? Math.min(1, (frames - f) / fadeOut) : 1;
  return Math.max(0, Math.min(up, down));
}

function CardView({ card }: { card: TitleCard }) {
  // sized like the export's card (studio/media/timeline.py:draw_card): title 1/11 of the height, subtext 1/24
  return (
    <AbsoluteFill style={{ background: card.background, color: card.color, alignItems: "center", justifyContent: "center",
                           fontFamily: "Arial, Helvetica, sans-serif", fontWeight: 700, textAlign: "center",
                           whiteSpace: "pre-line", gap: "3.3%" }}>
      {/* wrapped at the same width as the export's card (CARD_TEXT_WIDTH) */}
      <div style={{ fontSize: "9.1cqh", lineHeight: 1.15, maxWidth: "86%", overflowWrap: "anywhere" }}>{card.text}</div>
      {card.subtext && <div style={{ fontSize: "4.2cqh", lineHeight: 1.2, maxWidth: "86%", overflowWrap: "anywhere" }}>
        {card.subtext}</div>}
    </AbsoluteFill>
  );
}

/** The film as the export makes it: items with cuts or fades, and the tracks under them. */
export function Film({ items, sounds, duck }: FilmProps) {
  const speech = sounds.filter((s) => s.track === "dialogue");
  const underSpeech = (frame: number) => speech.some((s) => frame >= s.from && frame < s.from + s.frames);
  const series: ReactNode[] = [];
  items.forEach((item, i) => {
    if (i > 0 && item.overlapIn > 0) {
      series.push(<TransitionSeries.Transition key={`fade-${item.key}`} presentation={fade()}
                                               timing={linearTiming({ durationInFrames: item.overlapIn })} />);
    }
    series.push(
      <TransitionSeries.Sequence key={item.key} durationInFrames={item.frames}>
        {item.card ? <CardView card={item.card} /> : item.take && (
          <AbsoluteFill style={{ background: "black" }}>
            <Html5Video src={item.take.src} trimBefore={item.take.trimBefore} pauseWhenBuffering
                        volume={(f) => item.take!.volume * ramp(f, item.frames, item.overlapIn, item.overlapOut)}
                        style={{ width: "100%", height: "100%", objectFit: "contain" }} />
          </AbsoluteFill>
        )}
      </TransitionSeries.Sequence>,
    );
  });
  return (
    <AbsoluteFill style={{ background: "black", containerType: "size" }}>
      <TransitionSeries>{series}</TransitionSeries>
      {sounds.map((s) => (
        <Sequence key={s.key} from={s.from} durationInFrames={Math.max(1, s.frames)} layout="none">
          <Audio src={s.src} trimBefore={s.trimBefore} pauseWhenBuffering
                 volume={(f) => s.volume * ramp(f, s.frames, s.fadeIn, s.fadeOut)
                   * (duck && s.track === "music" && underSpeech(s.from + f) ? DUCKED : 1)} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
}

/** The preview player: the same frames, order and fades the export renders. */
export const CutPlayer = forwardRef<PlayerRef, FilmProps & { frames: number; width: number; height: number }>(
  function CutPlayer({ frames, width, height, ...film }, ref) {
    return (
      <Player ref={ref} component={Film} inputProps={film} durationInFrames={Math.max(1, frames)} fps={FPS}
              compositionWidth={width} compositionHeight={height} controls acknowledgeRemotionLicense
              numberOfSharedAudioTags={8}
              style={{ width: "100%", aspectRatio: `${width} / ${height}`, background: "black" }} />
    );
  },
);
