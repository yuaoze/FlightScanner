import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  createExperience,
  deleteExperience,
  fetchExperiences,
  updateExperience,
  type ExperienceBody,
  type ExperienceUpdateBody,
} from '../api/purchases';

export function useExperiences(status?: string) {
  return useQuery({
    queryKey: ['experiences', status ?? 'all'],
    queryFn: () => fetchExperiences(status ? { status } : { status: '' }),
    staleTime: 60 * 1000,
  });
}

export function useCreateExperience() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ExperienceBody) => createExperience(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['experiences'] });
    },
  });
}

export function useUpdateExperience() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: ExperienceUpdateBody }) =>
      updateExperience(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['experiences'] });
    },
  });
}

export function useDeleteExperience() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => deleteExperience(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['experiences'] });
    },
  });
}
