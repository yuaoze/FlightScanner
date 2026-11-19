import { useState } from 'react';
import bundledPhotos from '../../assets/destination-images.json';

interface Photo {
  image: string;
  title: string;
  author: string;
  source_url: string;
  license: string;
  license_url: string;
  modifications: string;
}

const photos: Record<string, Photo> = bundledPhotos;

interface Props {
  destination: string;
  imageUrl: string | null;
  gradient: string;
}

export function DestinationImage({ destination, imageUrl, gradient }: Props) {
  const photo = photos[destination];
  const source = photo?.image ?? (imageUrl?.includes('picsum.photos') ? null : imageUrl);
  const [failedSource, setFailedSource] = useState<string | null>(null);
  const unavailable = !source || failedSource === source;

  return (
    <figure className="absolute inset-0" style={{ background: gradient }}>
      {source && !unavailable && (
        <img
          src={source}
          alt={photo ? `${destination} · ${photo.title}` : `${destination}风景`}
          width={960}
          height={600}
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onError={() => setFailedSource(source)}
          className="absolute inset-0 h-full w-full object-cover transition-transform duration-700 group-hover:scale-105"
        />
      )}
      <div
        className="absolute inset-0"
        style={{
          background: unavailable
            ? 'radial-gradient(circle at 20% 30%, rgba(255,255,255,0.25) 0%, transparent 50%), radial-gradient(circle at 80% 80%, rgba(255,255,255,0.2) 0%, transparent 40%)'
            : 'linear-gradient(180deg, rgba(0,0,0,0.15) 0%, rgba(0,0,0,0.55) 100%)',
        }}
      />
      {photo && !unavailable && (
        <figcaption
          className="absolute inset-x-0 top-0 z-20 flex justify-center gap-2 bg-black/60 px-2 py-1 text-[8px] text-white/80 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100"
          title={photo.modifications}
        >
          <a href={photo.source_url} target="_blank" rel="noopener noreferrer">
            摄影：{photo.author}
          </a>
          {photo.license_url ? (
            <a href={photo.license_url} target="_blank" rel="noopener noreferrer">{photo.license}</a>
          ) : <span>{photo.license}</span>}
        </figcaption>
      )}
    </figure>
  );
}
